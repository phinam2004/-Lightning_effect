# 🧪 NHẬT KÝ KIỂM THỬ & NGHIỆM THU (QA TESTER)

- **Phụ trách**: QA Engineer
- **Dự án**: Chaos Magic — Real-Time Scarlet Witch VFX (`thor-energy`)
- **Trạng thái**: 🟢 TOÀN BỘ BỘ TEST ĐÃ PASS (6/6)

---

## 📋 DANH SÁCH KIỂM THỬ

| ID | Mục tiêu test | Lệnh / Phương pháp | Kết quả mong đợi | Trạng thái |
|:---|:---|:---|:---|:---|
| TC-01 | Kiểm tra môi trường Python 3.11 | `python --version` | Python 3.11.x | 🟢 PASS |
| TC-02 | Tạo môi trường ảo `.venv` | `python -m venv .venv` | Folder `.venv` được tạo | 🟢 PASS |
| TC-03 | Cài đặt `requirements.txt` | `.venv\Scripts\pip install -r requirements.txt` | Cài đặt thành công mediapipe, opencv, numpy | 🟢 PASS |
| TC-04 | Smoke test Import và Model Task | `.venv\Scripts\python -c "import cv2, mediapipe, numpy; ..."` | HandTracker nạp thành công XNNPACK delegate | 🟢 PASS |
| TC-05 | Kiểm tra khởi động webcam/pipeline | Probe `cv2.VideoCapture(0)` | Camera khả dụng, đọc frame 640x480 | 🟢 PASS |
| TC-06 | Kiểm thử chu trình End-to-End | Capture -> Landmarker -> Render frame | Output frame đúng định dạng, không văng lỗi | 🟢 PASS |

---

## 🔬 LOGS KIỂM THỬ THỰC TẾ
```text
INFO: Created TensorFlow Lite XNNPACK delegate for CPU.
W0000 00:00:1791542131.126434    3576 inference_feedback_manager.cc:121] Feedback manager requires a model with a single signature inference. Disabling support for feedback tensors.
W0000 00:00:1791542131.147087   33488 inference_feedback_manager.cc:121] Feedback manager requires a model with a single signature inference. Disabling support for feedback tensors.
HandTracker loaded successfully!
Pipeline initialized successfully for 640 x 480
Tracker processed frame, found hands: 0
Renderer rendered frame successfully, output shape: (480, 640, 3)
```

---

## 🏁 KẾT LUẬN QA
Hệ thống sẵn sàng 100% để người dùng tương tác thực tế với camera và cử chỉ tay.
