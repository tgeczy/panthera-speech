"""Numerical and guest-ABI oracles, using generated data only.

NumPy's LAPACK eigensolver is independent of the host's Jacobi rotations.
Residuals and orthogonality also check degenerate eigenspaces without requiring
an arbitrary choice of eigenvector signs or bases to match.
"""
import os
from pathlib import Path
import subprocess

import pytest

np = pytest.importorskip("numpy")
ROOT = Path(__file__).resolve().parents[2]
HOSTS = [Path(os.environ.get("PANTHERA_TEST_HOST", ROOT / "build/tiger_host.exe")),
         Path(os.environ.get("PANTHERA_TEST_UC_HOST", ROOT / "build/uc/tiger_host_uc.exe"))]


@pytest.fixture(params=HOSTS, ids=["native", "guest-bridge"])
def host(request):
    if not request.param.is_file():
        pytest.skip(f"build the host first: {request.param}")
    return request.param


def probe(host, a, job="V", which="A", triangle="U", il=1, iu=None, vl=0, vu=1):
    n = len(a)
    text = f"{n} {job} {which} {triangle} {il} {n if iu is None else iu} {vl} {vu}\n"
    text += " ".join(format(float(x), ".9g") for x in a.flat)
    run = subprocess.run([str(host), "--linalg-check"], input=text, text=True,
                         capture_output=True, timeout=20)
    assert run.returncode == 0, run.stdout + run.stderr
    return {parts[1]: np.array([float(x) for x in parts[2:]])
            for line in run.stdout.splitlines() if line.startswith("[linalg]")
            for parts in [line.split()]}


@pytest.mark.parametrize("kind", ["dense", "zero", "identity", "repeated", "small", "large"])
@pytest.mark.parametrize("n", [1, 3, 12])
def test_eigenpairs(host, kind, n):
    rng = np.random.default_rng(417 + n)
    a = rng.normal(size=(n, n))
    a = ((a + a.T) / 2).astype(np.float32)
    if kind == "zero": a[:] = 0
    if kind == "identity": a = np.eye(n, dtype=np.float32)
    if kind == "repeated": a = np.ones((n, n), dtype=np.float32)
    if kind == "small": a *= np.float32(1e-30)
    if kind == "large": a *= np.float32(1e30)
    # Poison the unused triangle. A solver reading both halves cannot pass.
    supplied = a.copy()
    supplied[np.tril_indices(n, -1)] = np.nan
    got = probe(host, supplied)
    assert got["query"].tolist() == [0, 26*n, 10*n]
    assert got["result"][0] == 0
    w, z = got["values"], got["vectors"].reshape(n, n)
    expected = np.linalg.eigvalsh(a.astype(np.float64))
    scale = max(float(np.max(np.abs(a))), 1e-35)
    np.testing.assert_allclose(w / scale, expected / scale, atol=2e-6, rtol=2e-6)
    np.testing.assert_allclose((a / scale) @ z, z * (w / scale), atol=3e-6, rtol=3e-6)
    np.testing.assert_allclose(z.T @ z, np.eye(n), atol=3e-6)


@pytest.mark.parametrize("which", ["I", "V"])
@pytest.mark.parametrize("job", ["N", "V"])
def test_selection_and_lower_triangle(host, which, job):
    a = np.array([[4, 1, -2], [1, 3, .5], [-2, .5, 6]], dtype=np.float32)
    supplied = a.copy()
    supplied[np.triu_indices(3, 1)] = np.nan
    got = probe(host, supplied, job, which, "L", il=2, iu=3, vl=3, vu=9)
    expected = np.linalg.eigvalsh(a.astype(np.float64))
    expected = expected[1:] if which == "I" else expected[(expected > 3) & (expected <= 9)]
    np.testing.assert_allclose(got["values"], expected, rtol=2e-6)
    if job == "V":
        z = got["vectors"].reshape(3, len(expected))
        np.testing.assert_allclose(a @ z, z * got["values"], atol=2e-6)
    else:
        assert not len(got["vectors"])


def test_empty_and_invalid_arguments(host):
    empty = probe(host, np.empty((0, 0)), which="I", iu=0)
    assert empty["query"].tolist() == [0, 1, 1]
    assert empty["result"][0] == 0
    assert not len(empty["values"])
    assert probe(host, np.eye(2), job="X")["query"][0] == -1
    assert probe(host, np.eye(2), which="I", il=0)["query"][0] == -9


def test_matrix_multiply_layout_transpose_padding_and_beta_zero(host):
    got = probe(host, np.eye(1))
    a = np.array([[(i*3+j-2)/4 for j in range(4)] for i in range(2)])
    b = np.array([[(i*2-j+1)/8 for j in range(3)] for i in range(4)])
    for row in range(2):
        for ta in range(2):
            for tb in range(2):
                for zero in range(2):
                    expected = np.full(64, 777.0)
                    result = .75 * (a @ b)
                    for i in range(2):
                        for j in range(3):
                            expected[i*7+j if row else j*7+i] = (
                                result[i, j] - (0 if zero else .5 * (i+j/10)))
                    np.testing.assert_allclose(got[f"gemm{row}{ta}{tb}{zero}"], expected, atol=1e-6)
