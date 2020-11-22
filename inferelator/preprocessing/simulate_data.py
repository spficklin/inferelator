import numpy as np
import math
import itertools
from scipy import sparse as _sparse

from inferelator.utils import Debug, InferelatorData
from inferelator import MPControl


def make_data_noisy(data, random_seed=42):
    """
    Generate a new data object of random data which matches the provided data

    :param data: Raw read data
    :type data: InferelatorData
    :return: Simulated data
    :rtype: InferelatorData
    """

    # Calculate probability vector for gene expression
    # Discrete sampling for count data

    sample_counts = data.sample_counts

    # Normalize to mean counts per sample and sum counts per gene by matrix multiplication
    p_vec = (np.mean(sample_counts) / sample_counts).reshape(1, -1) @ data.expression_data

    if data._is_integer:

        Debug.vprint("Simulating integer count data", level=0)

        # Flatten and convert counts to a probability vector
        p_vec = p_vec.flatten()
        p_vec = p_vec / p_vec.sum()

        data.expression_data = _sim_ints(p_vec, sample_counts, sparse=data.is_sparse, random_seed=random_seed)

    else:

        Debug.vprint("Simulating float data", level=0)

        # Flatten and convert counts to a mean vector
        p_vec = p_vec.flatten()
        p_vec /= data.num_obs

        data.expression_data = _sim_float(p_vec, data.gene_stdev, data.num_obs, random_seed=random_seed)


def _sim_ints(prob_dist, n_per_row, sparse=False, random_seed=42):

    if not np.isclose(np.sum(prob_dist), 1.):
        raise ValueError("Probability distribution does not sum to 1")

    ncols = len(prob_dist)

    def _sim_rows(n_vec, seed):
        row_data = np.zeros((len(n_vec), ncols), dtype=np.int32)

        rng = np.random.default_rng(seed=seed)
        col_ids = np.arange(ncols)

        for i, n in enumerate(n_vec):
            row_data[i, :] = np.bincount(rng.choice(col_ids, size=n, p=prob_dist))

        return _sparse.csr_matrix(row_data) if sparse else row_data

    ss = np.random.SeedSequence(random_seed)
    sim_data = MPControl.map(_sim_rows, _row_gen(n_per_row), _ss_gen(ss))

    return _sparse.hstack(sim_data) if sparse else np.hstack(sim_data)


def _sim_float(gene_centers, gene_sds, nrows, random_seed=42):

    ncols = len(gene_centers)
    assert ncols == len(gene_sds)

    def _sim_cols(cents, sds, seed):
        rng = np.random.default_rng(seed=seed)
        return rng.normal(loc=cents, scale=sds, size=(nrows, len(cents)))

    ss = np.random.SeedSequence(random_seed)

    return np.vstack(MPControl.map(_sim_cols, _col_gen(gene_centers), _col_gen(gene_sds), _ss_gen(ss)))


def _row_gen(n_vec, chunksize=2000):
    _chunks = math.ceil(len(n_vec) / chunksize)
    for i in range(_chunks):
        yield n_vec[i * chunksize: min(len(n_vec), (i + 1) * chunksize)]


def _col_gen(vals, chunksize=200):
    _chunks = math.ceil(len(vals) / chunksize)
    for i in range(_chunks):
        _start, _stop = i * chunksize, min(len(vals), (i + 1) * chunksize)
        yield vals[_start: _stop]


def _ss_gen(ss):
    while True:
        yield ss.generate_state(1)[0]
