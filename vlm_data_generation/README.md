# vlm_data_generation — sinh QA nháp bằng VLM (Bước ③)

Notebook Google Colab dùng **Gemini** (`gemini-3-flash-preview`, qua SDK
`google-genai`) để xem trực tiếp từng clip đã qua tiền xử lý ở
[`../clip_pipeline`](../clip_pipeline) và sinh ra một bộ QA nháp theo đúng 10
nhóm taxonomy của đề tài (xem [`../docs/EG-TrafficQA-VN_report.md`](../docs/EG-TrafficQA-VN_report.md)).

Đây là **Bước ③** trong 12 bước của pipeline gán nhãn — output là nháp, không
phải ground truth, và sẽ được audit lại ở Bước ④–⑧ (checklist, guideline,
majority vote, agreement rate).

## Notebook làm gì

1. Upload nhiều clip video cùng lúc (qua Colab file picker).
2. Với mỗi clip: upload lên Gemini Files API, gửi kèm prompt (xem
   `PROMPT_TEMPLATE` trong notebook) yêu cầu model:
   - Mô tả quan sát trước (scene description) — bắt buộc, để đối chiếu model
     có thực sự "nhìn" video hay không.
   - Sinh đúng 10 câu hỏi trắc nghiệm (mỗi nhóm S/E/N/C/V/O/R/Attr/Prev/Con
     một câu), 4 đáp án, 1 đúng, kèm `reasoning_type` và nhãn `answerable`
     sơ bộ.
   - Trả về JSON đúng schema — không thêm text ngoài JSON.
3. Parse JSON, gom kết quả tất cả clip vào `vqa_dataset_gemini_v2.json`.
4. Hiển thị lại từng clip kèm QA sinh ra để kiểm tra nhanh bằng mắt (không
   phải review chính thức — review chính thức là Bước ⑥ Majority Vote).

## Chạy trên Google Colab (khuyến nghị)

1. Mở `VLM_DataGeneration.ipynb` bằng Colab.
2. Vào **Secrets** (icon chìa khoá bên trái) → thêm `GOOGLE_API_KEY` (API key
   Gemini, cần quota/billing đủ cho video input).
3. Chạy tuần tự các cell. Ở cell upload, giữ **Ctrl/Cmd** để chọn nhiều clip
   cùng lúc — nên trỏ vào các clip đã export từ `clip_pipeline/export/<batch>/`.
4. File `vqa_dataset_gemini_v2.json` sẽ xuất hiện trong file panel bên trái,
   tải về để đưa vào Bước ④.

## Chạy local (thay Colab)

Notebook dùng hai API riêng của Colab cần thay khi chạy local/Jupyter thường:

| Trong notebook | Thay bằng khi chạy local |
|---|---|
| `google.colab.userdata.get('GOOGLE_API_KEY')` | đọc từ biến môi trường, vd `os environ.get("GOOGLE_API_KEY")` (đặt qua `.env` + `python-dotenv`, hoặc export trực tiếp trong shell) |
| `google.colab.files.upload()` | liệt kê file trực tiếp từ thư mục, vd `video_paths = sorted(Path("videos").glob("*.mp4"))` |

Cài đặt:

```bash
cd vlm_data_generation
python -m venv venv
venv/bin/pip install -r requirements.txt   # macOS/Linux
# venv/Scripts/pip install -r requirements.txt   # Windows
export GOOGLE_API_KEY=xxxx   # hoặc set trong .env
```

Sau đó chạy `jupyter notebook VLM_DataGeneration.ipynb` và sửa 2 chỗ ở bảng
trên trước khi chạy.

## Output

`vqa_dataset_gemini_v2.json` — mảng object, mỗi object ứng với 1 clip:

```jsonc
{
  "video_id": "clip_0007_t01.mp4",
  "scene_description": "...",
  "questions": [
    {
      "category": "S|E|N|C|V|O|R|Attr|Prev|Con",
      "question": "...",
      "options": { "A": "...", "B": "...", "C": "...", "D": "..." },
      "correct_answer": "A|B|C|D",
      "reasoning_type": "perception|recognition|summarization|causal|rule_based|outcome_assessment|behavioral|attribution|preventive|conclusive",
      "answerable": "answerable|ambiguous|not_answerable"
    }
    // ... đúng 10 câu, 1 câu / nhóm
  ],
  "answerability_note": {
    "hardest_category": "S|E|N|C|V|O|R|Attr|Prev|Con",
    "explanation": "..."
  }
}
```

## Lưu ý

- Đây là **QA nháp** — không dùng trực tiếp làm ground truth. Bắt buộc đi qua
  Bước ④ (checklist) và Bước ⑥ (majority vote ≥3 người) trước khi chốt GT
  (Bước ⑧), theo đúng pipeline mô tả trong đề cương.
- Prompt yêu cầu model chỉ chọn "Không thể xác định" khi thực sự không có
  bằng chứng trong video (không phải vì không chắc chắn 100%) — annotator
  cần kiểm tra lại đúng tiêu chí này khi vote.
- Nhóm Attribution/Conclusion chỉ dựa trên bằng chứng thị giác quan sát
  được, không phải phán quyết pháp lý — giữ nguyên ràng buộc này khi viết
  guideline ở Bước ⑤.
- Video/clip đưa vào đây là tư liệu bản quyền bên thứ ba (đã ẩn danh hoá ở
  `clip_pipeline`), chỉ phục vụ nghiên cứu — không commit clip hay
  `vqa_dataset_gemini_v2.json` chứa dữ liệu thật lên repo (đã gitignore).
