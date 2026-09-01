import io, os, numpy as np, cv2, math
# Путь от самого файла теста: вшитый путь к моей машине делал тест
# незапускаемым на малине, а прогонять его нужно как раз там.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
src = io.open(os.path.join(_ROOT, "tracker.py"), encoding="utf-8").read()
ns = {"cv2": cv2, "np": np, "math": math, "FLOW_MIN_POINTS": 3,
      "FLOW_ERR_MAX": 20.0, "FLOW_MAX_STEP": 30.0, "FLOW_WINDOW_PAD": 72, "FLOW_WINDOW_ENABLED": True}
exec("def flow_predict(" + src.split("def flow_predict(")[1].split("\ndef estimate_size_at_position")[0], ns)
win_fn = ns["flow_predict"]

# полнокадровый вариант — тот, что был раньше
def full_fn(prev_g, cur_g, pts, cx, cy):
    nxt, st, err = cv2.calcOpticalFlowPyrLK(prev_g, cur_g, pts, None,
        winSize=(15,15), maxLevel=2,
        criteria=(cv2.TERM_CRITERIA_EPS|cv2.TERM_CRITERIA_COUNT,14,0.03))
    st = st.reshape(-1).astype(bool)
    old_, new_ = pts[st].reshape(-1,2), nxt[st].reshape(-1,2)
    keep = err[st].reshape(-1) < 20.0
    old_, new_ = old_[keep], new_[keep]
    dx = float(np.median(new_[:,0]-old_[:,0])); dy = float(np.median(new_[:,1]-old_[:,1]))
    return True, cx+dx, cy+dy

rng = np.random.default_rng(7)
prev = (rng.random((480, 640)) * 255).astype(np.uint8)
prev = cv2.GaussianBlur(prev, (5,5), 0)
SHIFT_X, SHIFT_Y = 4, -3
M = np.float32([[1,0,SHIFT_X],[0,1,SHIFT_Y]])
cur = cv2.warpAffine(prev, M, (640,480))

patch = prev[200:280, 300:380]
corners = cv2.goodFeaturesToTrack(patch, maxCorners=25, qualityLevel=0.004,
                                  minDistance=2, blockSize=3)
pts = (corners + np.float32([300, 200])).astype(np.float32)
print("точек:", len(pts), " истинный сдвиг: dx=%+d dy=%+d" % (SHIFT_X, SHIFT_Y))

okf, fx, fy = full_fn(prev, cur, pts.copy(), 340.0, 240.0)
okw, wx, wy = win_fn(prev, cur, pts.copy(), 340.0, 240.0)
print("  полный кадр: cx=%.3f cy=%.3f" % (fx, fy))
print("  окно:        cx=%.3f cy=%.3f" % (wx, wy))
print("  расхождение: %.4f px" % math.hypot(fx-wx, fy-wy))
assert okf and okw
assert math.hypot(fx-wx, fy-wy) < 0.05, "оконный поток обязан совпасть с полнокадровым"
assert abs((fx-340.0) - SHIFT_X) < 0.5 and abs((fy-240.0) - SHIFT_Y) < 0.5, "сдвиг определён неверно"
print("\nOK: окно даёт тот же результат, что полный кадр")
