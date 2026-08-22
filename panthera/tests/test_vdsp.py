# -*- coding: utf-8 -*-
"""Accelerate, checked against numpy rather than against itself.

Lion's concatenative path finds its WSOLA overlap point by **FFT cross-
correlation**, where Leopard's worked in the time domain. That is eleven
Accelerate functions Leopard never imports, and without them Alex, Bruce and
Agnes all die at the same instruction in `MTMBModRateWsola::ModifyRate`.

Ten of the eleven are one-liners. `fft_zrip` is not, and it is the reason this
file exists: a hand-written FFT that is subtly wrong produces plausible
numbers, and plausible numbers in a cross-correlation produce a peak in the
wrong place, which is a voice that sounds slightly off rather than a crash.
So the host prints what its own code computes and **numpy computes the
expected answer by a completely different route** -- the same discipline the
dyld interpreter was built with.

Both sides generate the inputs from the same formula, so nothing has to be
parsed back out of the C side except results.

Two conventions worth stating, because they are where this would go wrong:

* `fft_zrip` is **packed real**: after a forward transform `realp[0]` is DC
  and `imagp[0]` is Nyquist, both real, sharing the slot that would otherwise
  hold bin 0's imaginary part -- which is always zero for real input, so the
  format wastes nothing.
* vDSP's real forward transform is **scaled by 2** against the textbook DFT.
  For the engine's purposes that is invisible -- it feeds the result to
  `vDSP_maxvi`, which returns the *index* of the peak, and a uniform scale
  cannot move an argmax -- but getting it wrong would still be wrong, and
  wrong in a way nothing downstream would report.
"""
import math
import os
import subprocess

import pytest

numpy = pytest.importorskip("numpy")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
HOST = os.path.join(ROOT, "build", "tiger_host.exe")

#: The one input formula both sides use.  Deliberately not a pure sine: a
#: single tone lands its energy in one bin and would hide almost any error.
def signal(n):
    return numpy.array([math.sin(0.1 * i) + 0.5 * math.cos(0.37 * i)
                        for i in range(n)], dtype=numpy.float32)


@pytest.fixture(scope="module")
def out():
    """-> {case: [floats]} from the host's own arithmetic."""
    if not os.path.isfile(HOST):
        pytest.skip("tiger_host.exe not built; run sh build.sh")
    run = subprocess.run([HOST, "--vdsp-check"], capture_output=True,
                         text=True, encoding="utf-8", timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    got = {}
    for line in run.stdout.splitlines():
        if not line.startswith("[vdsp]"):
            continue
        parts = line.split()
        got[parts[1]] = [float(x) for x in parts[2:]]
    assert got, run.stdout + run.stderr
    return got


def close(a, b, tol=2e-3):
    a, b = numpy.asarray(a, dtype=numpy.float64), \
        numpy.asarray(b, dtype=numpy.float64)
    assert a.shape == b.shape, (a.shape, b.shape)
    scale = max(1.0, float(numpy.abs(b).max()))
    worst = float(numpy.abs(a - b).max()) / scale
    assert worst < tol, "worst relative error %.2e\n got %s\nwant %s" % (
        worst, a[:8], b[:8])


# -- the interleaved/split conversions ------------------------------------

def test_ctoz_splits_even_and_odd(out):
    """`ctoz` reads an interleaved array as complex: even real, odd imaginary."""
    n = 16
    x = signal(n)
    close(out["ctoz"], numpy.concatenate([x[0::2], x[1::2]]))


def test_ztoc_is_the_inverse(out):
    """Round-tripping must give the original array back exactly."""
    close(out["ztoc"], signal(16))


# -- the FFT --------------------------------------------------------------

@pytest.mark.parametrize("n", [8, 16, 64, 256])
def test_fft_zrip_forward_matches_numpy(n, out):
    """Packed real forward transform, scaled by two.

    numpy's `rfft` returns N/2+1 bins; vDSP returns N/2 slots with Nyquist
    folded into `imagp[0]`. Unpacking one to the other is where a convention
    error shows up immediately.
    """
    f = numpy.fft.rfft(signal(n).astype(numpy.float64))
    want = numpy.empty(n, dtype=numpy.float64)
    want[0] = 2.0 * f[0].real                 # DC
    want[n // 2] = 2.0 * f[n // 2].real       # Nyquist, in imagp[0]
    for k in range(1, n // 2):
        want[k] = 2.0 * f[k].real
        want[n // 2 + k] = 2.0 * f[k].imag
    close(out["fftfwd%d" % n], want)


@pytest.mark.parametrize("n", [8, 16, 64, 256])
def test_fft_zrip_round_trips(n, out):
    """Forward then inverse, scaled by 1/(2n), is the identity.

    The engine does exactly this -- correlate in the frequency domain, come
    back -- so a transform that is self-consistent but not a DFT would pass
    this and fail the test above. Both are needed.
    """
    close(out["fftrt%d" % n], signal(n))


# -- the vector helpers ---------------------------------------------------

def test_zvcmul_conjugates_the_first_argument(out):
    """`zvcmul` multiplies by the **conjugate** of A, which is what makes it
    a correlation rather than a convolution. Getting this backwards mirrors
    the result and puts the peak at -lag."""
    n = 8
    x, y = signal(n), signal(n + 3)[3:]
    a = x[0::2] + 1j * x[1::2]
    b = y[0::2] + 1j * y[1::2]
    c = numpy.conj(a) * b
    close(out["zvcmul"], numpy.concatenate([c.real, c.imag]))


def test_maxvi_returns_value_and_index(out):
    """The index is the half that matters: it is the correlation peak."""
    x = signal(32)
    got = out["maxvi"]
    assert len(got) == 2
    assert abs(got[0] - float(x.max())) < 1e-4
    assert int(got[1]) == int(x.argmax())


def test_vma_multiplies_then_adds(out):
    n = 8
    a, b, c = signal(n), signal(n + 1)[1:], signal(n + 2)[2:]
    close(out["vma"], a * b + c)


def test_sve_sums(out):
    close(out["sve"], [float(signal(16).sum())])


def test_vclip_bounds_both_ends(out):
    close(out["vclip"], numpy.clip(signal(8), -0.25, 0.75))


def test_vramp_walks_by_a_step(out):
    close(out["vramp"], numpy.arange(8, dtype=numpy.float64) * 0.25 + 1.5)


def test_catlas_sset_fills(out):
    close(out["sset"], numpy.full(8, 3.5))
