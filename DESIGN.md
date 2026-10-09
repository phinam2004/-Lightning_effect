---
title: Chaos Magic VFX — Design System & Visual Contract
version: 1.0.0
last_updated: "2026-10-09"
description: >
  Single Source of Truth for visual aesthetics, color tokens, particle dynamics,
  and rendering hierarchy for Chaos Magic (Scarlet Witch real-time VFX).
tokens:
  colors:
    core:
      bgr: [40, 20, 200]
      hex: "#C81428"
      name: "Scarlet Crimson Core"
      usage: "Deep energy core and interior emitter color"
    mid:
      bgr: [60, 60, 235]
      hex: "#EB3C3C"
      name: "Vibrant Scarlet"
      usage: "Mid-energy body for swirls and particle streams"
    edge:
      bgr: [90, 140, 255]
      hex: "#FF8C5A"
      name: "Solar Orange Flame"
      usage: "Outer halo, dissipating sparks and ember tips"
    glyph:
      bgr: [160, 200, 255]
      hex: "#FFC8A0"
      name: "Warm Radiant White-Pink"
      usage: "Eldritch glyph rings and concentrated palm node"
    debris:
      bgr: [70, 70, 90]
      hex: "#5A4646"
      name: "Obsidian Shards"
      usage: "Solid telekinesis floating fragments"
    flare_hot:
      bgr: [235, 250, 255]
      hex: "#FFFAEB"
      name: "Supernova White Core"
      usage: "Ignition burst hot center"
    flare_ray:
      bgr: [50, 60, 255]
      hex: "#FF3C32"
      name: "Crimson Ray Starburst"
      usage: "Radial ignition flare spikes"
    link_core:
      bgr: [255, 90, 20]
      hex: "#145AFF"
      name: "Arcane Electric Blue"
      usage: "Dual-hand entanglement lightning bolt core"
    link_edge:
      bgr: [255, 230, 140]
      hex: "#8CE6FF"
      name: "Pale Cyan Corona"
      usage: "Lightning discharge outer glow"
  geometry:
    min_swirl_radius: 45.0
    max_swirl_radius: 95.0
    glyph_ring_inner: 42.0
    glyph_ring_outer: 84.0
    distortion_max_radius: 120.0
  timing:
    fist_extinguish_seconds: 1.0
    flare_duration_seconds: 0.55
    target_framerate: 60
---

# 🔮 Chaos Magic VFX — Visual Design Specification

## 1. Triết Lý Thiết Kế (Aesthetic Philosophy)
Ứng dụng tái hiện chân thực hiệu ứng **Chaos Magic** của Wanda Maximoff (Scarlet Witch) trong vũ trụ điện ảnh Marvel.
- **Không xâm lấn (Localized Guardrail)**: Toàn bộ hiệu ứng được giới hạn cục bộ trong bán kính quanh lòng bàn tay. Khung hình webcam nguyên bản của người dùng tuyệt đối **không bị ám màu, làm tối, mờ hay che phủ toàn màn hình**.
- **Động học tự nhiên (Vectorized Particle Dynamics)**: Sự kết hợp giữa trường xoáy logarithmic, nhiễu loạn Perlin, và lực hút quỹ đạo tạo cảm giác dòng năng lượng sống động, hữu cơ thay vì vòng lặp tĩnh.
- **Ánh xạ vật lý quang học (HDR Glow & Blending)**:
  - Năng lượng phát xạ dùng kỹ thuật **Additive Blending** kết hợp Gaussian Bloom nhiều tầng.
  - Các mảnh vụn vật thể telekinesis dùng **Alpha Blending** để che phủ hậu cảnh chân thực.

---

## 2. Bảng Màu Hệ Thống (Color Palette Tokens)

| Token | Giá trị HEX | Giá trị BGR (OpenCV) | Mục đích sử dụng |
|:---|:---|:---|:---|
| `CORE_COLOR` | `#C81428` | `[40, 20, 200]` | Tâm năng lượng đỏ thẫm (Crimson Core) |
| `MID_COLOR` | `#EB3C3C` | `[60, 60, 235]` | Thân luồng xoáy đỏ tươi (Vibrant Scarlet) |
| `EDGE_COLOR` | `#FF8C5A` | `[90, 140, 255]` | Vành nhiệt quang cam hồng (Solar Flame) |
| `GLYPH_COLOR` | `#FFC8A0` | `[160, 200, 255]` | Vòng ký tự rune phát sáng rực rỡ |
| `DEBRIS_COLOR`| `#5A4646` | `[70, 70, 90]` | Mảnh đá / tàn tích telekinesis |
| `FLARE_HOT`   | `#FFFAEB` | `[235, 250, 255]` | Tâm trắng nóng của tia bùng nổ khi kích hoạt |
| `FLARE_RAY`   | `#FF3C32` | `[50, 60, 255]` | Các tia nổ tỏa tròn khi đánh thức ma thuật |
| `LINK_CORE`   | `#145AFF` | `[255, 90, 20]` | Lõi sét năng lượng liên kết hai bàn tay |
| `LINK_EDGE`   | `#8CE6FF` | `[255, 230, 140]` | Vầng hào quang cyan bao quanh tia sét |

---

## 3. Thứ Tự Phối Ghép Lớp Đồ Họa (Layer Compositing Order)

Mỗi khung hình được xử lý theo 6 tầng nghiêm ngặt:
1. **Webcam Layer**: Nguồn video gốc không thay đổi.
2. **User Silhouette**: Người dùng trong khung hình giữ nguyên độ tương phản tự nhiên.
3. **Local Distortion**: Trường bẻ cong không gian cục bộ quanh bàn tay bằng `cv2.remap` với mặt nạ radial smoothstep.
4. **Telekinesis Debris**: Mảnh vỡ quỹ đạo xoay quanh bàn tay, dùng alpha occlusion.
5. **Chaos Swirl & Embers**: Hàng nghìn hạt năng lượng phát sáng với Additive Blending.
6. **Glyph Rings & Core Glow**: Vòng rune ma thuật và lõi sáng trung tâm trên cùng.
