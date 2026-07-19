"""data-prep — dataset workshop: synthetic generation + refine toolbox for api_v3."""

import os

# Cap BLAS/thread pools BEFORE numpy is imported anywhere (model2vec pulls in
# numpy from M4 on). The app runs single-worker, often next to api_v3 on the
# same machine; an unbounded BLAS pool would oversubscribe the CPU during
# embedding batches. setdefault keeps real environment variables authoritative.
for _thread_var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_thread_var, "1")

__version__ = "0.1.0"
