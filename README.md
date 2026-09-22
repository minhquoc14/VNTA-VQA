# VNTA-VQA

**An Evidence-Grounded Open-Ended Video QA Benchmark for Accident Scene
Understanding in Vietnam**

VNTA-VQA là bộ dữ liệu Hỏi-Đáp dạng văn bản tự do (open-ended) trên video
tai nạn/near-miss giao thông tại Việt Nam. Khác với các benchmark hiện có
(SUTD-TrafficQA, MM-AU, VRU-Accident, RoadSceneVQA), mỗi cặp QA ở đây được
gắn **evidence keyframe** và nhãn **answerability** (answerable / ambiguous /
not-answerable), nhằm phục vụ đồng thời hai nhóm đối tượng: lực lượng điều
tra (tường thuật, tra cứu bằng chứng) và cơ sở đào tạo lái xe (phân tích
nguyên nhân, phòng tránh).

Tài liệu nghiên cứu đầy đủ (vấn đề nghiên cứu, related work, metric chống
modality collapse, RQ, thiết kế thực nghiệm...) nằm trong [`docs/`](./docs).

## Repo này chứa gì

Repo gồm hai phần đầu của pipeline 12 bước gán nhãn — **thu thập/tiền xử lý
clip** và **sinh QA nháp bằng VLM**:

```
.
├── clip_pipeline/         Bước ①–② — web app local (Streamlit): tải video,
│                          cắt clip, xoá overlay, review, export
├── vlm_data_generation/   Bước ③ — notebook Colab: dùng Gemini sinh QA nháp
│                          10 nhóm cho từng clip đã qua clip_pipeline
└── docs/                  Báo cáo nghiên cứu, taxonomy, thiết kế judge/metric
```

Hai module **độc lập** về mặt chạy (không import lẫn nhau), nối với nhau qua
dữ liệu: output của `clip_pipeline` (`export/<batch>/*.mp4`) là input của
`vlm_data_generation`.

## Pipeline gán nhãn end-to-end (12 bước)

| Bước | Tên | Ở đâu trong repo |
|---|---|---|
| ① | Thu thập dữ liệu (YouTube/dashcam/CCTV) | [`clip_pipeline/`](./clip_pipeline) |
| ② | Lọc, ẩn danh (blur biển số/mặt), cắt clip 5–30s | [`clip_pipeline/`](./clip_pipeline) |
| ③ | Sinh QA nháp bằng VLM (10 nhóm + keyframe ứng viên + answerability nháp) | [`vlm_data_generation/`](./vlm_data_generation) |
| ④ | Checklist soạn thảo — chuẩn hoá QA nháp, chèn câu not-answerable |  |
| ⑤ | Team A viết Guideline (rubric đúng/sai, tiêu chí keyframe & answerability) |  |
| ⑥ | Team B Majority Vote (≥3 người/clip) | *(chưa có trong repo)* |
| ⑦ | Tính Agreement Rate (% đồng thuận, keyframe IoU, answerability agreement) | |
| ⑧ | Chốt GT (confirmed / loại bỏ / ambiguous → trục U) | *(chưa có trong repo)* |
| ⑨ | Xây & validate LLM/VLM Judge (GEPA optimize, Spearman ≥ 0.8) |  |
| ⑩ | Human baseline (blind vs full video) |  |
| ⑪ | Chia Train/Val/Test + báo cáo thống kê |  |
| ⑫ | Thực nghiệm: baseline + proposed method + ablation |  |

Các bước ④–⑫ (đánh giá, judge pipeline, phương pháp RL Keyframe Selection +
Pseudo-CoT Distillation, thực nghiệm) được mô tả chi tiết trong
[`docs/EG-TrafficQA-VN_report.md`](./docs/EG-TrafficQA-VN_report.md), sẽ có
code riêng khi triển khai.

## Taxonomy 10 nhóm câu hỏi

10 nhóm nội dung + 1 trục cắt ngang answerability (U), gắn lên mọi câu:

| Mã | Nhóm | Phục vụ | Độ khó GT |
|---|---|---|---|
| S | Scene / Context | Cả hai | Khách quan cao |
| E | Entities | Cả hai | Khách quan cao |
| N | Narrative | Cả hai | Vừa |
| C | Causal | Cả hai | Vừa |
| V | Violation | Điều tra | Vừa |
| O | Outcome | Cả hai | Khách quan |
| R | Response | Điều tra | Vừa |
| Attr | Attribution (bên nào góp phần CHÍNH gây va chạm, dựa trên bằng chứng thị giác — không phán lỗi pháp lý) | Điều tra | Chủ quan — soft-label, abstain |
| Prev | Prevention (lẽ ra nên làm gì để tránh va chạm) | Dạy lái | Chuẩn tắc — đa đáp án |
| Con | Conclusion (phân tích bên nào có khả năng gây va chạm, dựa trên bằng chứng thị giác) | Cả hai | Khách quan |
| **U** | Answerability (trục ngang: answerable / ambiguous / not-answerable) | Cả hai | Không đồng thuận annotator → ambiguous |

Chi tiết design rationale nằm trong
[`docs/02_question_taxonomy.html`](./docs/02_question_taxonomy.html).

## Bắt đầu nhanh

**1. Cắt & tiền xử lý clip** — xem [`clip_pipeline/README.md`](./clip_pipeline/README.md):

```bash
cd clip_pipeline
python -m venv venv && venv/Scripts/pip install -r requirements.txt
venv/Scripts/streamlit run app.py
```

**2. Sinh QA nháp từ clip đã export** — xem
[`vlm_data_generation/README.md`](./vlm_data_generation/README.md): mở
`vlm_data_generation/VLM_DataGeneration.ipynb` trên Google Colab, thêm
`GOOGLE_API_KEY` vào Colab Secrets, upload clip từ
`clip_pipeline/export/<batch>/`, chạy các cell.

## Phạm vi & giới hạn đạo đức

- Quy mô mục tiêu: **~3000 clip, ~30000 cặp QA**, video 5–30 giây, thu thập
  từ camera hành trình, CCTV và mạng xã hội.
- Chỉ Hỏi-Đáp dạng văn bản tự do — **không** bounding box, segmentation hay
  dự đoán quỹ đạo.
- Biển số và khuôn mặt được ẩn danh hoá ở bước tiền xử lý
  (`clip_pipeline`) trước khi đưa vào bất kỳ bước nào khác.
- Dữ liệu chỉ phục vụ nghiên cứu; **không** thay thế kết luận điều tra
  chính thức của cơ quan chức năng. Nhóm câu hỏi Attribution/Conclusion chỉ
  là suy luận dựa trên bằng chứng thị giác, không phải phán quyết pháp lý.
- Video gốc là tư liệu bản quyền bên thứ ba — giữ ở local, không
  redistribute (`clip_pipeline/work/`, `export/` đều đã gitignore).

## Đội ngũ

- Nguyễn Minh Quốc — MSSV 23521304
- Nguyễn Văn Quyền — MSSV 23521329
- Hướng dẫn: TS. Đỗ Trọng Hợp, ThS. Nguyễn Ngọc Quý
- Trường Đại học Công nghệ Thông tin, ĐHQG-HCM · Thời gian thực hiện:
  07/09/2026 – 26/12/2026
