# clip_pipeline — thu thập & tiền xử lý clip (Bước ①–②)

Ứng dụng web chạy **local** (Streamlit), biến các video tổng hợp tai nạn
giao thông trên YouTube thành các clip ngắn, sạch (đã xoá logo/đếm số, đã
blur biển số & khuôn mặt), sẵn sàng cho bước sinh QA (`../vlm_data_generation`).

Tài liệu chi tiết: [`CLAUDE.md`](./CLAUDE.md) (constitution) ·
[`GUIDE.md`](./GUIDE.md) (hướng dẫn chạy + rubric review) ·
[`architecture.md`](./architecture.md) · [`TODO.md`](./TODO.md)

---

## Cài đặt

```bash
cd clip_pipeline
python -m venv venv
venv/Scripts/pip install -r requirements.txt   # Windows
# venv/bin/pip install -r requirements.txt     # macOS/Linux
```

Yêu cầu **ffmpeg** và **ffprobe** trong `PATH`. Có GPU NVIDIA thì dùng được
`h264_nvenc`; không có thì đổi encoder sang `libx264` ở sidebar.

YouTube chặn bot ở phần lớn IP, nên bước tải video cần thêm:

- **Cookies** — export từ trình duyệt đã đăng nhập ra `cookies.json`
  (định dạng JSON của extension) hoặc `cookies.txt` (Netscape). Cả hai đều
  được chấp nhận; JSON sẽ tự động convert. Đặt đường dẫn ở sidebar mục
  *YouTube access*.
- **Node hoặc Deno trong `PATH`** — thử thách "n" của YouTube cần một JS
  runtime thật. Thiếu cái này, việc trích xuất sẽ lỗi
  `The page needs to be reloaded`.

## Chạy

```bash
venv/Scripts/streamlit run app.py
```

Đó là toàn bộ giao diện — cấu hình, xếp hàng, chạy, review, export. Không có
command-line flag nào khác.

1. **Queue** — dán URL hoặc upload file `.txt` (mỗi dòng một URL)
2. **Settings** (sidebar) — bật/tắt và chỉnh tham số, rồi Save
3. **Run** — Start; progress và log cập nhật ngay trong app
4. **Review** — clip dài nhất được ưu tiên (>15s = TOP). Đánh dấu đoạn cần
   giữ, sau đó Approve hoặc Reject. Bắt buộc phải quyết định mới đi tiếp —
   không có Skip.
5. **Export** — ghi ra `export/<batch>/` kèm `manifest.json`

Hướng dẫn từng bước và quy tắc *cắt ở đâu* nằm trong [`GUIDE.md`](./GUIDE.md).

## Cách phân đoạn hoạt động

**PySceneDetect** `ContentDetector`, dùng tham số mặc định. Một tín hiệu duy
nhất nên mọi ranh giới đều là `MEDIUM`.

Đường counter-fusion đã bị gỡ sau khi test trên footage thật. Ở video
*Camera Giao thông*, bộ đếm `#03` in cứng vào hình là **thoáng qua** — chỉ
hiện vài giây đầu mỗi đoạn — nên phương sai theo thời gian trên từng pixel
không phát hiện được, và nếu phát hiện sai sẽ khiến hai tín hiệu "độc lập"
tương quan với nhau, làm tăng ảo độ tin cậy trên một tín hiệu đội hai vai.

Vì vậy thứ tự review dựa trên **độ dài clip**, không dựa trên confidence.

## Xoá overlay

Hai lớp, áp dụng cùng lúc:

1. **Vùng đã calibrate** — phương sai theo thời gian trên từng pixel tìm ra
   logo tĩnh. Ngưỡng là percentile (1% vùng ổn định nhất khung hình) cộng
   sàn tuyệt đối, vì logo thật thường alpha-blend: cảnh vẫn hiện qua nên
   không pixel nào thực sự tĩnh tuyệt đối. Phương sai sàn đo trên footage
   thật là 258; ngưỡng tuyệt đối cũ 18 không tìm ra gì cả.
2. **Dải cố định** — cấu hình ở sidebar theo tỉ lệ khung hình, áp dụng cho
   *mọi* video. Phủ đồng hồ in cứng, bộ đếm `#03` và tên camera nguồn — vốn
   xuất hiện ngắt quãng nên bước 1 không thấy được.

Đã verify trên footage thật: std vùng logo **79.6 → 18.8** (−76%), trong khi
một patch mặt đường cùng kích thước không đổi (17.71 → 17.68).

## Cấu trúc thư mục

```
clip_pipeline/
  app.py               Streamlit UI — toàn bộ giao diện
  run_pipeline.py       subprocess entrypoint (Streamlit spawn cái này)
  vqa/
    config.py          load/save config.json + config_hash
    db.py               SQLite: channels, videos, clips, reviews
    urls.py             YouTube URL -> video_id (khoá dedup)
    media.py             ffmpeg/ffprobe: probe, sample, cut, blur, encode, trim
    pipeline.py         orchestration theo từng video, có resume
    review.py            materialise đoạn đã approve / xoá đoạn bị reject
    stages/
      download.py       yt-dlp (cookies + JS runtime)
      calibrate.py       vùng overlay tĩnh (theo từng kênh, có cache)
      shots.py           PySceneDetect
      fuse.py             chính sách ranh giới/clip, lọc đoạn trống
  work/<video_id>/       (gitignored — sinh ra khi chạy)
    source.mp4           bản tải về
    *.json                cache từng stage (probe, shots, boundaries)
    clips/                bản gốc — cắt lossless, không bao giờ sửa
    delivered/            bản đã blur — cái reviewer xem
    trimmed/               THÀNH PHẨM — ghi khi Approve, xoá khi Reject
  export/<batch>/        (gitignored) clip đã export + manifest.json
```

## Ba tầng clip

| Thư mục | Là gì | Ai ghi | Có bị sửa không? |
|---|---|---|---|
| `clips/` | bản cắt lossless `-c copy` | pipeline | không bao giờ |
| `delivered/` | bản phái sinh đã blur | pipeline | re-encode mỗi lần chạy lại |
| `trimmed/` | thành phẩm cuối | **reviewer, khi Approve** | ghi đè khi approve lại, xoá khi Reject |

Đổi setting blur là re-derive từ master local: không tải lại, không detect
lại. Các đoạn đã approve sẽ tự động được cắt lại từ `delivered` mới, nên đổi
setting không bao giờ để lại output cũ trong `trimmed/`.

## Resume

Mỗi stage ghi ra một file có thể đoán trước và sẽ bị skip nếu file đó đã tồn
tại. Kill run bất cứ lúc nào và chạy lại — nó tự vào lại đúng chỗ dừng, không
bao giờ tải lại từ đầu. Muốn ép chạy lại một stage thì xoá file JSON tương
ứng trong `work/<video_id>/`.

## Test

```bash
venv/Scripts/pytest
```

## Lưu ý

- `h264_nvenc` không có CRF; `-cq` là tương đương (sidebar: *NVENC cq*).
  Mặc định là 23.
- Blur overlay chỉ xoá *khả năng đọc*, không xoá *sự hiện diện* — pixel dưới
  overlay composite không bao giờ lộ ra ở bất kỳ frame nào nên không thể
  khôi phục. Desaturate + darken để xoá dấu hiệu màu thương hiệu.
- Headless mode (tắt review) ghi clip là `UNREVIEWED`, không bao giờ
  `APPROVED`, và không sinh gì trong `trimmed/`.
- Video tải về là tư liệu bản quyền bên thứ ba, chỉ phục vụ nghiên cứu học
  thuật. Giữ ở local, không redistribute. `cookies.*`, `work/`, `export/` và
  `config.json` đều đã gitignore.
