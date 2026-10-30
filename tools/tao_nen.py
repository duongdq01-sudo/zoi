"""Ve anh nen phong canh ruong bac thang (thay anh chup, server khong co internet).
Chay: python tao_nen.py <file_ra.jpg>"""
import sys
import numpy as np
from PIL import Image, ImageFilter

W, H = 1920, 1080
rng = np.random.default_rng(7)
y = np.arange(H)[:, None].astype(float)
x = np.arange(W)[None, :].astype(float)

def mix(a, b, t):
    t = np.clip(t, 0, 1)[..., None]
    return np.array(a, float) * (1 - t) + np.array(b, float) * t

# troi: suong xam-xanh nhat, sang o goc tren trai (nang)
img = mix((150, 162, 140), (96, 116, 84), (y / (H * .55)) * np.ones_like(x))
sun = np.exp(-(((x - W * .12) ** 2) / (2 * (W * .35) ** 2) + ((y - H * .05) ** 2) / (2 * (H * .45) ** 2)))
img = img + sun[..., None] * np.array([30, 28, 15])

def noise1d(n, scale, amp, seed):
    r = np.random.default_rng(seed)
    k = r.normal(size=n // scale + 3)
    xs = np.linspace(0, len(k) - 3, n)
    i = xs.astype(int); f = xs - i
    f = f * f * (3 - 2 * f)
    return (k[i] * (1 - f) + k[i + 1] * f) * amp

# cac bac thang: tu xa (tren, nhat, mo) toi gan (duoi, dam, ro)
n_bac = 22
for j in range(n_bac):
    t = j / (n_bac - 1)
    base = H * (0.10 + 0.95 * t ** 1.15)
    slope = (x[0] - W * (0.15 + 0.5 * t)) * (0.18 - 0.1 * t)
    line = base + slope * 0.35 + noise1d(W, 160, 18 + 30 * t, 100 + j) + noise1d(W, 40, 4 + 6 * t, 300 + j)
    band = 40 + 70 * t
    d = y - line[None, :]
    mask = (d > 0)
    # mep bac sang vang-xanh, chan bac toi
    g = np.clip(d / band, 0, 1)
    sang = (190 + 25 * (1 - t), 205 - 10 * t, 90 - 30 * t)
    toi = (40 - 15 * t, 70 - 20 * t, 25 - 10 * t)
    col = mix(sang, toi, g ** 0.6)
    xa = 1 - t                     # phu suong cho bac xa
    col = col * (1 - 0.55 * xa ** 2) + np.array([150, 162, 140]) * 0.55 * xa ** 2
    img = np.where(mask[..., None], col, img)

# hang cay toi o goc duoi phai
tree = np.exp(-(((x - W * .92) ** 2) / (2 * (W * .14) ** 2))) * np.exp(-(((y - H) ** 2) / (2 * (H * .22) ** 2)))
img = img * (1 - 0.55 * tree[..., None]) + np.array([20, 40, 18]) * 0.55 * tree[..., None]
# vignette
v = 0.86 - 0.42 * (((x - W / 2) / (W / 1.4)) ** 2 + ((y - H / 2) / (H / 1.2)) ** 2)
img = img * np.clip(v, .55, 1)[..., None]
# hat nhieu nhe cho giong anh chup
img = img + rng.normal(0, 3.0, img.shape)

im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
im = im.filter(ImageFilter.GaussianBlur(1.6))
im.save(sys.argv[1], quality=82, optimize=True, progressive=True)
print(sys.argv[1], im.size)
