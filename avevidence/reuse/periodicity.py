"""Loop navigation candidates, never voice identity or beat perception."""
import numpy as np
from scipy.signal import correlate, find_peaks


def periodicity(x, rate=16000):
    hop = max(1, round(.01*rate))
    n = len(x)//hop
    if n < 60:
        return {"candidates": [], "status": "INSUFFICIENT_DURATION"}
    # Channel energy avoids anti-phase cancellation.
    envelope = np.sqrt(np.mean(x[:n*hop].reshape(n, hop, -1).astype(float)**2, axis=(1, 2)))
    envelope -= envelope.mean()
    if np.dot(envelope, envelope) < 1e-14:
        return {"candidates": [], "status": "NO_MODULATED_ENERGY"}
    corr = correlate(envelope, envelope, mode="full", method="fft")[n-1:]
    corr /= max(corr[0], 1e-18)
    lo, hi = 20, min(n//3, 1000)
    peaks, _ = find_peaks(corr[lo:hi], height=.3, distance=8)
    peaks = sorted((int(p+lo) for p in peaks), key=lambda p: -corr[p])[:5]
    return {"status": "CANDIDATE_ONLY", "method": "10 ms per-channel-energy envelope autocorrelation",
            "candidates": [{"period_s": p*hop/rate, "correlation": float(corr[p]),
                            "repeat_count_estimate": len(x)/(p*hop)} for p in peaks],
            "meaning": "Repetition candidates; music rhythm alone can cause these peaks. Verify with segment matches."}
