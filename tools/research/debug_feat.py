import numpy as np
import sys
sys.path.insert(0, "tests")
from test_sync import synth_rpm_profile, synth_audio_from_rpm
from media_tools.sync import AUDIO_RATE, FEATURE_HZ

telemetry_rpm = synth_rpm_profile(300)
i0 = int(84.3 * FEATURE_HZ)
clip_rpm = telemetry_rpm[i0: i0 + 1200]
audio = synth_audio_from_rpm(clip_rpm)
print("audio stats", audio.min(), audio.max(), audio.std(), len(audio))

rate = AUDIO_RATE
hop = int(rate / FEATURE_HZ)
win = 2048
n_frames = (len(audio) - win) // hop + 1
idx = np.arange(win)[None, :] + hop * np.arange(n_frames)[:, None]
frames = audio[idx] * np.hanning(win)[None, :]
rms = np.sqrt((frames ** 2).mean(axis=1))
spec = np.abs(np.fft.rfft(frames, axis=1))
freqs = np.fft.rfftfreq(win, 1 / rate)
band = (freqs >= 60) & (freqs <= 800)
peak = freqs[band][np.argmax(spec[:, band], axis=1)]

# expected firing freq of clip rpm at frame times
t_frames = (hop * np.arange(n_frames)) / rate
rpm_at = np.interp(t_frames, np.arange(len(clip_rpm)) / FEATURE_HZ, clip_rpm)
expect = rpm_at / 60 * 2
print("rms corr with rpm:", np.corrcoef(rms, rpm_at)[0, 1])
print("peak corr with rpm:", np.corrcoef(peak, rpm_at)[0, 1])
print("peak[0:10]", peak[:10])
print("expect[0:10]", expect[:10])
print("rms[0:10]", rms[:10])
print("rpm_at[0:10]", rpm_at[:10])
