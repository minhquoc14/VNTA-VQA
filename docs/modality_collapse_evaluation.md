# Đánh giá Modality Collapse cho EG-TrafficQA-VN — Từ Δ-accuracy đến Metric có nền tảng thông tin

> Trả lời từng ý: **Shortcut Score là gì → Visual Gain tính thế nào → Δ-accuracy có đủ để chứng minh không → Chặn trên & KL divergence → Bảng metric mới → Pipeline chuẩn.**
> Mọi metric ở đây đều gắn với **định lý/định nghĩa toán học** để khi thầy hỏi "sao em biết ngưỡng này đủ" thì em có căn cứ, không phải chọn tay.

---

## PHẦN 0 — Đặt lại vấn đề bằng một hình duy nhất

Modality collapse = model trả lời đúng **không nhờ video**. Toàn bộ việc đánh giá quy về đo *"video đóng góp bao nhiêu thông tin thực sự vào câu trả lời"*. Có 3 tầng đo, từ THÔ đến TINH:

```
                     THÔNG TIN VIDEO ĐÓNG GÓP  (cần đo)
                                 │
     ┌───────────────────────────┼────────────────────────────┐
     │                           │                            │
 [Tầng 1] ACCURACY-based    [Tầng 2] PROBABILITY-based   [Tầng 3] CAUSAL/INFO-based
   Δ = acc_full − acc_blind   KL(P_video ‖ P_blind)        PMI / Mutual Information
   Visual Gain, Blind Gap     per-sample, có dấu           I(Answer ; Video | Q)
                                                          
   ▲ rẻ, dễ hiểu             ▲ nhạy hơn, per-sample       ▲ đúng bản chất nhất
   ▼ thô, mất thông tin      ▼ cần logit/xác suất         ▼ khó ước lượng, cần nhiều mẫu
   ▼ nhị phân đúng/sai       ▼ chưa causal                ▼ đắt
```

**Ý chính em cần nắm ngay:** Δ-accuracy (Tầng 1) là **hệ quả bị lượng tử hoá** của thứ sâu hơn ở Tầng 2–3. Nó KHÔNG sai, nhưng nó là *cận dưới thô* của "lượng thông tin video đóng góp". Vì vậy câu hỏi của thầy — *"sao Δ này là đủ?"* — không có câu trả lời bằng chính Δ; phải trả lời bằng Tầng 2 hoặc 3. Đó là toàn bộ lý do phần này tồn tại.

---

## PHẦN 1 — Shortcut Score là gì (định nghĩa gốc + bản dịch cho benchmark của em)

### 1.1. Định nghĩa gốc (Korkut et al., 2026 — "From Accuracy to Visual Dependence")

Shortcut Score là điểm **ở mức từng câu hỏi** `q`, đo mức độ một câu "dễ bị đi tắt bằng ngôn ngữ". Công thức:

```
        S(q)  =  T(q)   −   V(q)
                 ▲          ▲
        Textual Evidence   Visual Necessity
        (text-only giải    (video có kéo xác suất
         được & tự tin?)    về đáp án đúng không?)
```

**Thành phần 1 — Textual Evidence Score `T(q)`** (càng cao càng dễ đi tắt):

```
          1
T(q) = ─────────  Σ    [Correct_m(q)] · (1 − H_norm,m(q))
        |M_LLM|  m∈LLM
                     └──────────┘   └──────────────┘
                     model text-only  độ TỰ TIN (entropy thấp
                     trả lời ĐÚNG?    = tự tin cao = shortcut mạnh)
```

- `H_m(q) = −Σ_k p_{m,k} log p_{m,k}` : entropy của phân phối đáp án model text-only.
- `H_norm = H_m / ln K` ∈ [0,1] : chuẩn hoá theo số lựa chọn K (K=5 với benchmark của em: 4 option + "Cannot be determined").
- Chỉ câu **text-only đúng** mới đóng góp. Đúng + rất tự tin (entropy≈0) → `T(q)≈1` → shortcut nặng.

**Thành phần 2 — Visual Necessity Score `V(q)`** (càng cao càng "cần" video):

```
          1          Δ_m(q)                         
V(q) = ─────── Σ   ──────────                       
        |M_VLM| m   1 − 1/K                          

  với  Δ_m(q) = P_m(y* | video, q) − P_m(y* | blind, q)
                └───────────────┘   └───────────────┘
                xác suất gán cho     xác suất gán cho
                đáp án đúng KHI       đáp án đúng KHI
                CÓ video             KHÔNG video
```

- `y*` = đáp án đúng. `Δ_m` = video kéo xác suất-đúng lên bao nhiêu.
- Chia `(1 − 1/K)` để chuẩn hoá về [−1, 1] (mức tăng tối đa có thể).
- `V(q) > 0`: video giúp. `V(q) ≤ 0`: video vô dụng hoặc có hại.

**Lọc:** giữ câu có `S(q) ≤ τ` (họ chọn τ=0.1). Câu S cao = shortcut-prone = loại/sửa.

### 1.2. Vì sao S(q) tốt hơn "Δ-accuracy nhị phân" — điểm mấu chốt cho em

Δ-accuracy nhị phân (đúng→sai) **vứt mất thông tin**. Ví dụ hai câu cùng "text-only đúng, có-video cũng đúng" → Δ_acc = 0 cho cả hai, bị loại như nhau. Nhưng:

```
Câu A:  P(đúng|blind)=0.26 → P(đúng|video)=0.95   video KÉO MẠNH → GIỮ LẠI (tốt)
Câu B:  P(đúng|blind)=0.94 → P(đúng|video)=0.95   video vô dụng   → LOẠI (shortcut)

Δ_accuracy:  cả hai đều "đúng cả hai điều kiện" → nhìn giống hệt nhau (SAI LẦM)
S(q)      :  phân biệt được — vì nó nhìn XÁC SUẤT liên tục, không nhị phân
```

→ **Đây chính là câu trả lời đầu tiên cho thầy:** *"Δ-accuracy là trường hợp bị nhị-phân-hoá của một đại lượng liên tục trên xác suất. Em dùng dạng liên tục (S-score / KL) để không mất thông tin ở vùng biên."*

---

## PHẦN 2 — Visual Gain: cách tính, và nó KHÁC Δ-accuracy của em ở đâu

### 2.1. Hai định nghĩa ở hai mức khác nhau — đừng nhầm

```
┌─ MỨC DATASET (trung bình toàn bộ) ──────────────────────────┐
│                                                             │
│   Blind Gap   BG = Acc_blind − Acc_random    (=1/K)         │
│   Visual Gain VG = Acc_video − Acc_blind                    │
│                                                             │
│   → đúng cái Δ em đang định dùng: VG ≡ Δ(acc_full−acc_blind)│
└─────────────────────────────────────────────────────────────┘

┌─ MỨC CÂU HỎI (per-sample) ──────────────────────────────────┐
│                                                             │
│   V(q) = trung bình Δ_m(q) trên xác suất-đúng (mục 1.1)     │
│                                                             │
│   → mịn hơn, là cái nên dùng để LỌC từng câu                │
└─────────────────────────────────────────────────────────────┘
```

**Vậy Visual Gain của em (Δ-accuracy) = phiên bản mức-dataset, nhị-phân.** Nó ổn để BÁO CÁO tổng quan, nhưng KHÔNG đủ để *chứng minh* và KHÔNG đủ để *lọc câu*.

### 2.2. Đọc bản đồ BG–VG (cách trình bày kết quả rất mạnh cho paper)

Vẽ mỗi benchmark/subset thành 1 điểm trên mặt phẳng (BG, VG). Đây là hình "signature" của Korkut et al., em nên tái tạo cho EG-TrafficQA-VN:

```
   VG (Visual Gain) ↑ = video càng hữu ích
    │
 hi │        ★ VÙNG TỐT (mục tiêu của em)
    │        "grounded": BG thấp + VG cao
    │      ┌───────────────┐
    │      │  TrafficQA     │        ○ VRU-Accident
    │      │  (target)      │        (VG cao NHƯNG BG cũng cao
    │      └───────────────┘         → còn giải được bằng text)
  0 ┼───────────────────────────────────────────► BG →
    │                                    (Blind Gap = text-only
    │        ○ AccidentBench              solvability, càng cao càng xấu)
    │
 lo │                          ✗ MM-AU
    │                          VÙNG XẤU: BG cao + VG ÂM
    │                          (thêm video còn làm TỆ hơn)
    ▼
```

**Mục tiêu benchmark của em = kéo điểm về góc trên-trái (VG cao, BG thấp).** Toàn bộ pipeline design là để đạt điều đó — và em ĐO được sự dịch chuyển này trước/sau khi lọc → đó là bằng chứng định lượng cho "chống collapse".

---

## PHẦN 3 — Trả lời trực diện: "Sao em biết Δ này là đủ để tránh collapse?"

Đây là câu hỏi khó nhất của thầy. Có **3 tầng trả lời**, dùng dần theo mức độ bị vặn.

### 3.1. Tầng trả lời A — "Δ không phải ngưỡng em tự đặt, nó có baseline thống kê"

Δ tuyệt đối (ví dụ "Δ ≥ 10%") là **tự chọn tay → yếu**. Thay vào đó neo Δ vào **ý nghĩa thống kê**: video có làm accuracy tăng **có ý nghĩa** so với nhiễu lấy mẫu không?

**Kiểm định McNemar** (chuẩn cho so sánh 2 classifier trên cùng tập test — ở đây là "cùng model, có video vs không video"):

```
                    có-video ĐÚNG   có-video SAI
   blind ĐÚNG            a               b       ← b: blind đúng mà video làm sai
   blind SAI             c               d       ← c: video CỨU được câu blind sai
                                                    (c chính là "video có ích")

              (b − c)²
   χ² =  ───────────────      ~ χ²(1 dof)
               b + c
```

- Nếu `c ≫ b` (video cứu nhiều hơn phá) và χ² vượt ngưỡng (p<0.05) → **video đóng góp có ý nghĩa thống kê**, không phải may rủi.
- **Câu trả lời cho thầy:** *"Em không khẳng định một Δ cố định là đủ. Em kiểm định McNemar: nếu video không đóng góp, phân phối b,c phải đối xứng. Bác bỏ được giả thuyết đối xứng ở mức p<0.05 nghĩa là đóng góp của video vượt nhiễu lấy mẫu."*
- **Khả thi:** ⭐⭐⭐⭐⭐ rất cao. Chỉ cần bảng 2×2, vài dòng code. Nên làm ĐẦU TIÊN.

### 3.2. Tầng trả lời B — "Δ chỉ là cận dưới; đại lượng đúng là thông tin, và em ĐO nó bằng KL"

Đây là nơi ý tưởng KL của em (rất đúng hướng) vào cuộc. Xem PHẦN 4.

### 3.3. Tầng trả lời C — "Có chặn trên không?" — Có, và nó là giới hạn thông tin

Câu hỏi "chặn trên của Δ" thực chất là hỏi: *video có thể đóng góp TỐI ĐA bao nhiêu?* Trả lời: bị chặn bởi **lượng thông tin mà câu trả lời còn thiếu sau khi đã biết câu hỏi** — xem PHẦN 5 (Fano / mutual information). Đây là câu trả lời "đẳng cấp" nhất.

---

## PHẦN 4 — KL divergence giữa phân phối có/không video: DÙNG ĐƯỢC, và đây là cách làm đúng

Ý tưởng của em **rất tốt** và có nền tảng lý thuyết. Nhưng cần làm chuẩn 3 điểm dưới, nếu không sẽ bị phản biện.

### 4.1. Định nghĩa metric per-sample: Video Influence bằng KL

Với mỗi câu hỏi q, model cho 2 phân phối trên K đáp án:
- `P_video(· | q)` : có video
- `P_blind(· | q)` : không video

```
   VideoKL(q) = D_KL( P_video(·|q)  ‖  P_blind(·|q) )
                                                    
              = Σ_k  P_video(k) · log ( P_video(k) / P_blind(k) )
```

**Diễn giải:** đo "video làm phân phối niềm tin dịch chuyển bao xa khỏi phân phối chỉ-đọc-chữ". KL lớn = video thay đổi mạnh cách model nghĩ = câu này **video-dependent**.

```
  P_blind:  A ▓▓ B ▓▓▓▓▓▓ C ▓▓ D ▓ (E) ▓        ← đoán mò theo prior ngôn ngữ
  P_video:  A ▓  B ▓      C ▓▓▓▓▓▓▓▓▓ D ▓ (E) ▓  ← video dồn niềm tin về C
            └──────────────┬──────────────┘
                     KL lớn ⇒ video thực sự "nói điều gì đó mới"
```

### 4.2. BA lưu ý bắt buộc (không có thì bị bác)

**(1) KL bất đối xứng — chọn hướng có chủ đích.**
`D_KL(P_video ‖ P_blind)` ≠ `D_KL(P_blind ‖ P_video)`. Dùng **forward** (video ‖ blind) như trên: nó phạt nặng khi video dồn khối lượng vào chỗ mà blind gán ~0 (đúng ý "video tiết lộ điều blind không biết"). Nếu muốn đối xứng, dùng **Jensen–Shannon** (khuyến nghị cho paper vì bị chặn [0, log2], dễ chuẩn hoá):

```
   JS(q) = ½ D_KL(P_video ‖ M) + ½ D_KL(P_blind ‖ M),   M = ½(P_video + P_blind)
   JS ∈ [0, ln2]  → chia ln2 để về [0,1]  → dễ so sánh giữa câu, giữa model
```

**(2) KL đo ĐỘ DỊCH CHUYỂN, không đo ĐÚNG HƯỚNG.** Đây là bẫy chí mạng:

```
   video kéo niềm tin RA KHỎI đáp án đúng (làm model sai đi) → KL vẫn LỚN
   nhưng đây là video CÓ HẠI, không phải video hữu ích!
```

→ Phải **kết hợp KL với dấu của thay đổi xác suất-đúng**. Định nghĩa metric ký hiệu:

```
   SignedVideoGain(q) = sign( P_video(y*) − P_blind(y*) ) · JS_norm(q)
                        └────────────┬────────────┘   └────┬────┘
                        video kéo ĐÚNG HƯỚNG hay sai?    ĐỘ LỚN dịch chuyển
```

Đây là **metric mới em có thể claim**: gộp *hướng* (từ Δ xác suất-đúng) và *độ lớn* (từ JS) — vừa không mất thông tin như Δ-accuracy nhị phân, vừa không mù hướng như KL trần.

**(3) Cần logit/xác suất chuẩn hoá.** Phải lấy xác suất trên **token của các option letter** (A/B/C/D/E) rồi normalize — đúng như Korkut et al. làm (log-likelihood scoring per option). Model API không trả logit thì phải dùng model mở (Qwen2.5-VL, InternVL) — vốn cũng là cái em chạy blind-test.

### 4.3. Vì sao KL/JS trả lời được câu "sao Δ đủ" tốt hơn Δ

```
  Δ-accuracy:   chỉ biết "đổi đúng↔sai" — 1 bit, tại NGƯỠNG 0.5
  KL/JS     :   đo TOÀN BỘ sự dịch chuyển phân phối — dùng mọi bit thông tin
                → nhạy với câu mà video "củng cố niềm tin đúng" dù chưa đổi nhãn
```

**Câu trả lời cho thầy (bản mạnh):** *"Δ-accuracy chỉ phát hiện thay đổi khi nó vượt ngưỡng quyết định 0.5, nên nó là hàm bậc thang mất thông tin. Em đo trực tiếp độ dịch chuyển phân phối bằng Jensen–Shannon divergence (chuẩn hoá [0,1]), có gắn dấu theo hướng về đáp án đúng. Nhờ vậy 'đủ video-dependence' không còn là một ngưỡng Δ tự chọn, mà là một phân phối JS mà em có thể kiểm định khác 0 có ý nghĩa."*

---

## PHẦN 5 — "Chặn trên" và nền tảng lý thuyết thông tin (câu trả lời đẳng cấp nhất)

Câu hỏi thầy — *"có thực nghiệm nào suy ra chặn trên của Δ không?"* — quy về **lý thuyết thông tin**. Đại lượng đúng đằng sau tất cả là **thông tin tương hỗ có điều kiện**:

```
   I( A ; Video | Q )  =  "biết video giúp giảm bao nhiêu bất định về đáp án A,
                           SAU KHI đã biết câu hỏi Q"
```

- `I(A;Video|Q) = 0` ⟺ **modality collapse hoàn toàn** (video không thêm thông tin nào về đáp án khi đã có câu hỏi). Đây là **định nghĩa hình thức của collapse** — mạnh hơn mọi Δ.
- Benchmark tốt ⟺ `I(A;Video|Q)` **lớn** cho mọi câu.

### 5.1. Chặn trên của "video có thể giúp bao nhiêu" — Bất đẳng thức Fano

Fano cho ta liên hệ giữa **xác suất sai tối thiểu** và **thông tin còn thiếu**:

```
   H(A|Q, Video)  ≤  H_b(P_error) + P_error · log(K−1)
```

Đảo lại theo hướng dùng được cho em: gọi `H(A|Q)` = bất định về đáp án khi CHỈ có câu hỏi (không video). Thì phần accuracy mà video có thể đóng góp bị chặn:

```
                       I(A ; Video | Q)
   VisualGain_max  ≲  ──────────────────   (bậc độ lớn; qua Fano)
                         log(K−1)

   trong đó   I(A;Video|Q) = H(A|Q) − H(A|Q,Video)
                             └───┬──┘   └────┬────┘
                          bất định khi    bất định khi
                          CHỈ có Q        có cả Q+Video
```

**Ý nghĩa cực kỳ dùng được:**
- Nếu `H(A|Q)` đã nhỏ (câu hỏi + option đã gần như lộ đáp án) → **trần đóng góp của video thấp** → dù model xịn đến đâu VG cũng không cao được → **câu này bản chất là shortcut-prone, phải loại từ khâu THIẾT KẾ, không cứu được bằng model.**
- **Đây là câu trả lời hoàn hảo cho "chặn trên":** *"Chặn trên của Visual Gain không phải hằng số, nó bằng thông tin tương hỗ I(A;Video|Q) chia log(K−1) theo bất đẳng thức Fano. Câu nào H(A|Q) thấp thì trần thấp — nghĩa là em phải kiểm soát nó ở khâu sinh câu hỏi (làm option không lộ đáp án), chứ đo Δ ở khâu cuối là quá muộn."*

### 5.2. Ước lượng thực nghiệm I(A;Video|Q) — có khả thi không?

Có, bằng xấp xỉ qua chính các phân phối model (proxy). Ước lượng **giảm entropy trung bình**:

```
   Î(A;Video|Q) ≈  (1/N) Σ_q [ H(P_blind(·|q)) − H(P_video(·|q)) ]
                              └──────────┬──────────┘
                       entropy giảm bao nhiêu khi thêm video, trung bình toàn tập
```

- Đây là **Information Gain** cổ điển, rất dễ tính (chỉ cần 2 phân phối đã có sẵn ở PHẦN 4).
- **Cảnh báo:** đây là proxy dựa trên *niềm tin của model*, không phải MI thật của dữ liệu. Nhưng dùng để **so sánh tương đối giữa các subset / trước-sau lọc** thì hoàn toàn hợp lệ và đủ mạnh cho paper.
- Khả thi: ⭐⭐⭐⭐ (tái dùng đúng phân phối của PHẦN 4).

### 5.3. Sơ đồ tổng hợp toàn bộ quan hệ toán học

```
   H(A|Q)  ──────────────── bất định khi chỉ có câu hỏi (đo được từ P_blind)
      │
      │  trừ đi
      ▼
   H(A|Q,Video) ──────────── bất định khi có video (đo được từ P_video)
      │
      │  =
      ▼
   I(A;Video|Q) ──────────── THÔNG TIN VIDEO ĐÓNG GÓP  ★ đại lượng gốc
      │
      ├──(chia log(K−1), Fano)──►  chặn trên của Visual Gain
      │
      ├──(nhị phân hoá tại 0.5)──►  Δ-accuracy / Visual Gain  (Tầng 1, thô)
      │
      └──(per-sample, có dấu)────►  SignedVideoGain = sign·JS  (Tầng 2, mịn) ★ metric mới của em
```

Mọi metric em dùng đều là **hình chiếu của cùng một đại lượng I(A;Video|Q)**. Trình bày được sơ đồ này trong paper = thầy hết đường vặn "sao chọn metric này".

---

## PHẦN 6 — Bảng metric & thứ tự thực nghiệm (ưu tiên từ trên xuống)

Làm lần lượt. Mỗi bước là superset thông tin của bước trước, nên không phí công.

| # | Metric / Thực nghiệm | Công thức lõi | Trả lời câu hỏi nào | Chi phí | Khả thi | Ưu tiên |
|---|---|---|---|---|---|---|
| 1 | **Dataset-level BG & VG** | VG=Acc_v−Acc_b; BG=Acc_b−1/K | "Benchmark của em nằm ở đâu trên bản đồ collapse" | Rất thấp | ⭐⭐⭐⭐⭐ | **P0 — làm ngay** |
| 2 | **McNemar test** trên (b,c) | χ²=(b−c)²/(b+c) | "Đóng góp video có ý nghĩa thống kê không" | Rất thấp | ⭐⭐⭐⭐⭐ | **P0** |
| 3 | **Per-sample SignedVideoGain** | sign(Δp*)·JS_norm | "Lọc câu nào, giữ câu nào" (thay Δ nhị phân) | Thấp (cần logit) | ⭐⭐⭐⭐ | **P1** |
| 4 | **Shortcut Score S(q)=T−V** | mục 1.1 | "Điểm shortcut từng câu, có baseline paper" | Trung bình (cần panel LLM+VLM) | ⭐⭐⭐⭐ | **P1** |
| 5 | **Information Gain** Î(A;V\|Q) | mean(H_blind−H_video) | "Video đóng góp bao nhiêu BIT thông tin" | Thấp (tái dùng #3) | ⭐⭐⭐⭐ | **P2** |
| 6 | **Fano upper-bound check** | VG_max≲I/log(K−1) | "Chặn trên & câu nào bản chất không cứu được" | Trung bình | ⭐⭐⭐ | **P2** |
| 7 | **Calibration/ECE trên answerability** | ECE giữa confidence & GT U | "Model có biết mình không biết" (trục U của em) | Trung bình | ⭐⭐⭐ | **P3** |
| 8 | **Human ceiling / lower-bound** | acc người trên blind vs video | "Trần thật của tập, tách 'khó vì thiếu video' vs 'khó vì mơ hồ'" | Cao (cần người) | ⭐⭐ | **P3 (nếu còn thời gian)** |

**Nguyên tắc đọc bảng:** P0 đủ để viết được phần "benchmark của em video-dependent" ở mức tối thiểu bảo vệ được. P1 là mức nên có để paper mạnh. P2 là mức "thầy hết vặn". P3 là bonus.

---

## PHẦN 7 — Pipeline chuẩn: Ingestion → Evaluation (bản chốt, gắn metric vào từng chốt)

Điểm khác biệt so với luồng hiện tại của em: **evaluation modality collapse KHÔNG phải một bước cuối** — nó là **2 cổng (gate) tách rời**: một cổng ở khâu SINH (creation-side, chặn trước) và một cổng ở khâu KIỂM (auditing-side, chặn sau). Chống collapse ở khâu sinh rẻ hơn nhiều so với sửa ở khâu cuối.

```
 ┌──────────────┐   ┌───────────────────┐   ┌──────────────────┐
 │ 1. INGESTION │──►│ 2. PROCESSING     │──►│ 3. QA GENERATION │
 │  crawl video │   │ trim/dedup/anon   │   │  VLM draft QA    │
 └──────────────┘   │ + scene diversity │   │  + distractor    │
                    └───────────────────┘   └────────┬─────────┘
                                                     │
             ┌───────────────────────────────────────┘
             ▼
 ╔═══════════════════════════════╗   ← GATE A: CHẶN TRƯỚC (creation-side)
 ║ 4. ANTI-SHORTCUT AT CREATION  ║     rẻ nhất, hiệu quả nhất
 ║  • kiểm H(A|Q) không quá thấp ║     (Fano: nếu option đã lộ đáp án,
 ║    (option không lộ đáp án)   ║      video vô phương cứu → sửa NGAY)
 ║  • cân bằng phân phối đáp án  ║
 ║  • ép ≥X% câu counterfactual  ║
 ║  • chèn câu "Cannot determine"║
 ╚═══════════════╤═══════════════╝
                 ▼
 ┌───────────────────────────────┐
 │ 5. HUMAN ANNOTATION           │   evidence keyframe + answerability
 │  2 annotators + evidence      │   (bất đồng annotator = tín hiệu 'ambiguous')
 └───────────────┬───────────────┘
                 ▼
 ┌───────────────────────────────┐
 │ 6. ANNOTATOR AGREEMENT (IAA)  │   Cohen/Fleiss κ ≥ 0.6
 │  κ gate; <0.6 → quay lại 5    │   (cổng chất lượng, KHÔNG phải collapse)
 └───────────────┬───────────────┘
                 ▼
 ╔═══════════════════════════════╗   ← GATE B: CHẶN SAU (auditing-side)
 ║ 7. MODALITY-COLLAPSE AUDIT    ║     chạy panel model có/không video
 ║  P0: VG, BG, McNemar          ║────┐
 ║  P1: SignedVideoGain, S(q)    ║    │  câu shortcut-prone
 ║  P2: InfoGain, Fano check     ║    │  (S cao / VG≈0 / McNemar fail)
 ╚═══════════════╤═══════════════╝    │
                 │ giữ câu grounded    │
                 ▼                     ▼
 ┌───────────────────────────────┐  ┌──────────────────────┐
 │ 8. SPLIT & PACKAGE            │  │ 8'. EDIT / REGENERATE │
 │  train/val/test              │  │  sửa distractor,      │
 │  + báo cáo dịch chuyển        │  │  đổi câu, rồi quay    │
 │  (BG,VG) trước/sau lọc  ★     │  │  lại GATE A ──────────┼──► (vòng lặp)
 └───────────────────────────────┘  └──────────────────────┘
```

### 7.1. Ba điểm chỉnh so với luồng gốc của em

1. **Tách "evaluation collapse" thành GATE A + GATE B.** Luồng gốc của em chỉ có một chốt "evaluation modality collapse → edit QA". Đưa một phần lên GATE A (khâu sinh) giúp em **không phí công annotate những câu vốn đã hỏng về mặt thông tin** (H(A|Q) thấp) — annotate xong mới phát hiện thì tốn 2 tháng công vô ích.

2. **IAA (bước 6) và collapse-audit (bước 7) là HAI cổng khác nhau, đừng gộp.** κ đo *người có đồng ý với nhau không* (chất lượng nhãn). Collapse-audit đo *model có cần video không* (chất lượng chống-shortcut). Một câu có κ cao vẫn có thể shortcut-prone.

3. **Vòng lặp 8' → GATE A, không phải → bước 5.** Câu bị loại vì shortcut nên được sửa ở mức *thiết kế câu hỏi* (distractor/option), rồi kiểm lại H(A|Q) trước khi tốn công annotate lại.

### 7.2. Bản đồ "metric gắn ở đâu"

```
  GATE A (khâu sinh)   →  H(A|Q) proxy, cân bằng đáp án        [phòng bệnh]
  Bước 6 (IAA)         →  Cohen/Fleiss κ                        [chất lượng nhãn]
  GATE B (khâu kiểm)   →  VG, BG, McNemar, SignedVideoGain,     [chữa bệnh]
                          S(q), InfoGain, Fano-check
  Bước 8 (đóng gói)    →  báo cáo Δ(BG,VG) trước/sau lọc  ★      [bằng chứng cho paper]
```

Con số vàng để đưa vào paper: **"sau khi lọc bằng SignedVideoGain/S(q), BG giảm từ X→Y và VG tăng từ Z→W"** — đúng dạng bảng kết quả của Korkut et al. Đó là bằng chứng định lượng "benchmark của em thực sự chống được modality collapse", chứ không phải lời khẳng định suông.

---

## PHẦN 8 — Tóm tắt câu trả lời cho từng câu hỏi của thầy (bản bỏ túi)

| Thầy hỏi | Trả lời một câu | Dựa trên |
|---|---|---|
| Shortcut Score là gì? | Điểm per-câu S(q)=T(q)−V(q): text-only giải được (T) trừ đi mức video thực sự cần (V); cao = đi tắt bằng ngôn ngữ | PHẦN 1 |
| Visual Gain tính sao? | Mức dataset: VG=Acc_video−Acc_blind (chính là Δ của em); mức câu: V(q) trên xác suất-đúng | PHẦN 2 |
| Δ = acc_full − acc_text có đủ không? | Không, vì nó nhị-phân-hoá tại ngưỡng 0.5 nên mất thông tin vùng biên; là *cận dưới thô* | PHẦN 3.1, 4.3 |
| Sao biết Δ này là "đủ"? | Không neo vào một Δ cố định; neo vào McNemar (p<0.05) rằng đóng góp video vượt nhiễu lấy mẫu | PHẦN 3.1 |
| Có chặn trên không? | Có: VG_max ≲ I(A;Video\|Q)/log(K−1) theo Fano; câu H(A\|Q) thấp thì trần thấp → sửa từ khâu sinh | PHẦN 5.1 |
| Dùng KL 2 phân phối có/không video được không? | Được, nên dùng Jensen–Shannon chuẩn hoá [0,1], VÀ gắn dấu theo hướng đáp án đúng (KL trần mù hướng) | PHẦN 4 |

---

### Ghi chú nguồn
- Định nghĩa Shortcut Score, T(q), V(q), Blind Gap, Visual Gain, ngưỡng τ=0.1, bản đồ BG–VG: Korkut, Bravo Sarmiento, Kim, Akata — *"From Accuracy to Visual Dependence: Auditing and Filtering Modality Collapse in Traffic VideoQA"*, CTB@ICML 2026 (arXiv:2606.30220).
- Bất đẳng thức Fano, mutual information có điều kiện, Jensen–Shannon divergence: chuẩn lý thuyết thông tin (Cover & Thomas).
- McNemar test cho so sánh classifier ghép cặp: chuẩn thống kê.
- VRU-Accident (đối tượng so sánh): Kim, Abdelrahman, Abdel-Aty — ICCV 2025 (arXiv:2507.09815).
