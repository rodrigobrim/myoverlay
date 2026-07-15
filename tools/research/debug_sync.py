import numpy as np
import pandas as pd
import sys
sys.path.insert(0, "tests")
from test_sync import synth_rpm_profile, synth_audio_from_rpm
from media_tools.sync import audio_feature, rpm_feature, find_offset, FEATURE_HZ

telemetry_rpm = synth_rpm_profile(300)
true_lag_s = 84.3
i0 = int(true_lag_s * FEATURE_HZ)
clip_rpm = telemetry_rpm[i0: i0 + int(120 * FEATURE_HZ)]
audio = synth_audio_from_rpm(clip_rpm)

af = audio_feature(audio)
df = pd.DataFrame({"t_s": np.arange(len(telemetry_rpm)) / FEATURE_HZ, "rpm": telemetry_rpm})
rf = rpm_feature(df)
print("len rf", len(rf), "len af", len(af))

# direct alignment check: correlation of af vs rf slice at true lag
seg = rf[i0:i0+len(af)]
n = min(len(seg), len(af))
c = np.corrcoef(seg[:n], af[:n])[0,1]
print("raw pearson at true lag:", c)
cd = np.corrcoef(np.diff(seg[:n]), np.diff(af[:n]))[0,1]
print("diff pearson at true lag:", cd)

res = find_offset(rf, af, lag_window_s=(true_lag_s-120, true_lag_s+120))
print("found:", res)

# scan a few lags
x = np.diff(rf); y = np.diff(af)
num = np.correlate(x, y, mode="full")
sx = np.correlate(x*x, np.ones(len(y)), mode="full")
sy = np.correlate(np.ones(len(x)), y*y, mode="full")
score = num/np.sqrt(np.maximum(sx*sy,1e-12))
lags = np.arange(-(len(y)-1), len(x))
for target in [84.3, 189.1, 100.0]:
    k = np.argmin(np.abs(lags/FEATURE_HZ - target))
    print(f"score near {target}: {score[k]:.3f}")
top = np.argsort(score)[-5:]
print("top lags:", lags[top]/FEATURE_HZ, score[top])
