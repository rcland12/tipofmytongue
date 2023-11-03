import os
from typing import List, Union
from fastapi import FastAPI
import numpy as np
from pydantic import BaseModel
from enum import Enum

from app.embedding_utils import create_embedding, load_embeddings, load_word_dicts, count_populated
from app.query_utils import find_similar_words
from app.download_embeddings import download_embeddings

from mangum import Mangum

###########################
### App Dependencies
###########################

cache_path = "res/word_embeddings_cache.npz.chk_non_norm_466503"
dict_path = "res/words.txt"

# Download the embedding cache if it doesn't exist locally
if 'DOWNLOAD_CACHE_NAME' in os.environ and not os.path.exists(cache_path):
    cache_name=os.getenv("DOWNLOAD_CACHE_NAME")
    print(cache_name)
    download_embeddings(cache_name)

embeddings = load_embeddings(cache_path)
dictionary = load_word_dicts(dict_path)
print(f"Loaded {len(dictionary)} words from {dict_path}")

# The length of the embeddings will always match the dictionary.
# Some or all of the indexes may be populated
embeddings_count = count_populated(embeddings)
print(f"Loaded {embeddings_count} embeddings from {cache_path}")
if embeddings_count == 0:
    print("Cache empty! Exiting")
    exit(-1)


# TODO - Remove global dependencies & extract these into query_utils package

def similar_words(q, k=10):
   return find_similar_words(q, embeddings, dictionary, k)

def similar_svn(q, k=10, knn_count=100, c=0.1):
    # Use KNN to find the nearest knn_count words using a basic distance function
    # This helps narrow the search for the SVN, since training an SVN is expensive
    knn_results = similar_words(q, knn_count)
    local_embeddings = [embeddings[x[1]] for x in knn_results]


    from sklearn import svm
    # Append the query into the set of data to evaluate
    x = np.concatenate([q[None,...], local_embeddings])
    y = np.zeros(len(x))
    y[0] = 1 # We have 1 positive sample

    # Train the SVN
    clf = svm.LinearSVC(class_weight='balanced', verbose=False, dual=True, max_iter=10000, tol=1e-6, C=c)
    clf.fit(x, y)
    
    # Only run inference on the knn result embeddings (skip the query included in X)
    similarities = clf.decision_function(local_embeddings)
    sorted_ix = np.argsort(-similarities)

    # Build response format. Mirror the KNN result structure, but include similarity score instead of distance   
    matches = []
    for k in sorted_ix[:k]:
        knn_result_mapping = knn_results[k]
        matches.append((knn_result_mapping[0], knn_result_mapping[1], similarities[k]))
    
    return matches

###########################
### REST API
###########################

app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Hello World"}

class Function(str,Enum):
    start = 'start'
    more_like = "more_like"
    less_like = "less_like"

class Operation(BaseModel):
    id: str | None = None
    function: Function
    description: str
    results: list | None = None
    selected_words: list | None = None

@app.post("/operations")
async def create_operation(ops: List[Operation]):
    print(ops)
    # wasteful - should init   to empty
    q = create_embedding("king")

    for o in ops:
        op_embedding = create_embedding(o.description)
        match o.function:
            case Function.start:
                q = op_embedding
            case Function.less_like:
                q -= op_embedding
            case Function.more_like:
                q += op_embedding

    results = similar_svn(q)
    ops[-1].results = [{"word": x[0], "dist": x[2]} for x in results]

    return ops

handler = Mangum(app, lifespan="off")