# PERFORMANCE — 效能紀錄

> 量測日期：2026-10-18。量測機器：Intel Core i7-1355U 筆電，**Intel UHD Graphics 內顯**（ANGLE / Direct3D 11），Chromium（Playwright 1.64）。
> 原始數據在 `renders/acceptance/perf-*.json`，也可以用 `web/tests/fps-uncapped.mjs` 重跑。

## 1. 載入大小（硬性條件：首次載入 ≤ 25 MB，主場景 GLB ≤ 15 MB）

| 項目 | 大小 |
|---|---|
| `scene.glb`（建築、地形、爬藤、遠景；Meshopt） | **1.59 MB** ✅ |
| `trees.glb`（6 種樹型 × 2 LOD） | 0.20 MB |
| `collision.glb`（簡化碰撞網格） | 0.47 MB |
| 光照圖＋AO（6 張 WebP，2048／1024） | 0.95 MB |
| 材質貼圖（9 組 1K 色彩＋法線 WebP、4 張自製圖集、小地圖） | 約 4.3 MB |
| JS（three.js＋three-mesh-bvh＋應用程式，gzip 前 0.9 MB） | 約 0.25 MB（gzip） |
| **首次載入實際傳輸量（Resource Timing 量測）** | **7.3 MB** ✅ |
| `dist/` 總計（含 5 個頁面） | 8.5 MB |

未壓縮時 Blender 匯出的 GLB 為 5.6 MB，經 Meshopt 量化與壓縮後剩 1.6 MB。

## 2. 繪製負擔（硬性條件：draw calls ≤ 150）

| 視點 | draw calls | 三角面 |
|---|---|---|
| 正立面（facade） | 64 | 25.8 萬 |
| 西側松林（pines） | 46 | 24.5 萬 |
| 二樓陽台（balcony） | 62 | 26.3 萬 |
| 咖啡露台（today） | 59 | 25.6 萬 |
| 高畫質等級（high，含即時陰影） | 79 | 43.1 萬 |

全部 ≤ 150 ✅。樹木 430 株共用約 24 個 InstancedMesh draw call。

## 3. 影格率

### 桌機（1440 × 900，medium 等級，pixel ratio 1）

| 視點 | 垂直同步下（正式條件） | 解除幀率上限（看餘裕） |
|---|---|---|
| 正立面 | 60.1 fps | 117 fps |
| 西側松林 | 60.1 fps | 56 fps |
| 二樓陽台 | 60.0 fps | 86 fps |
| 咖啡露台 | — | 55 fps |

- 在這台內顯筆電上，大部分視點有餘裕，可以維持 60 fps。最密的松林與露台視點，原生解析度約 55 fps，動態解析度會把 pixel ratio 降到約 0.88 來補足 60 fps。獨立顯卡或 Apple M 系列可以用 high 等級（2048 陰影、更密的草）。
- 驗收時發現，每 0.25 秒重新上傳所有樹木實例矩陣，會讓 Intel 驅動程式停頓，造成平均 FPS 從 60 掉到 38。改成「視點移動超過 2 m，且 LOD 分組真的改變時才更新」之後，問題就解決了（見 DECISIONS.md）。

### 手機（390 × 844，medium 等級，pixel ratio 1.5）

| 條件 | 正立面 | 松林 | 陽台 | 露台 |
|---|---|---|---|---|
| 行動版視窗、垂直同步 | 60.0 | 60.0 | 60.1 | — |
| 解除上限、CPU 降速 4 倍 | 146 | 184 | 136 | 143 |
| 解除上限、CPU 降速 6 倍 | 109 | 135 | 106 | 111 |

**限制**：這是在筆電上用 Chromium 模擬行動裝置（視窗、觸控、UA、CPU 降速），GPU 仍然是筆電的內顯，沒有在實體 iPhone 12 上量測。iPhone 12（A14）的 GPU 效能和這顆 Intel UHD 同級或略高，而行動版每格只畫 0.74 MP（筆電是 1.3 MP），所以**推估**可以維持 30 fps 以上；達不到時，動態解析度會先把 pixel ratio 從 1.5 往下降到 0.7，再關閉陰影，持續低於 15 fps 時會建議改看 2D 版。建議上線後用實機再量一次（網址加 `?q=low` 可以比較低畫質等級）。

## 4. 依裝置自動調整

| 等級 | 判斷依據 | pixel ratio | 陰影 | 草叢數 | LOD 距離 |
|---|---|---|---|---|---|
| high | 桌機獨立顯卡 | ≤ 2 | 2048 | 26,000 | 75 m |
| medium | 桌機內顯／一般手機 | ≤ 1.25（桌機）／1.5（手機） | 無 | 12,000／9,000 | 55 m |
| low | 軟體繪圖、舊款手機 GPU | 1 | 無 | 4,000 | 38 m |

執行時每秒檢查 FPS：低於目標（桌機 55、手機 30）就把 pixel ratio 降 12%；長時間超過 57 再逐步調回去。

## 5. 超出預算時的刪減順序（目前不需要）

依任務規定：遠景細節（城市方塊、山脈）→ 樹木數量（`layout.json` 的 `forest_count`、`garden_count`）→ 貼圖解析度（`tools/optimize.mjs` 的 resize）。碰撞網格不刪減。

## 6. 線上載入比較（GitHub Pages vs Claude Artifact）

| | GitHub Pages | Claude Artifact |
|---|---|---|
| 網址 | https://triathlan226.github.io/pine-garden-3d/ | https://claude.ai/artifact/9sopdzQ8xUmi6xtxVDZrp9 |
| 存取 | 公開 | 私人（需登入；要分享請用頁面的 Share 選單） |
| 模型格式 | `.glb`（Meshopt） | `.gltf.json`（同一份資料改為 base64 內嵌，因為 Artifact 不提供 `.glb`），體積多約 0.7 MB |
| 檔案數／總量 | 45 個／8.5 MB | 45 個／9.2 MB |
| 快取標頭 | `Cache-Control: max-age=600`（GitHub 固定，無法自訂）；JS／CSS 檔名含 hash，模型與貼圖加 `?v=` 版本參數 | 由 Artifact 服務決定 |
| 冷啟動到可操作（本機量測，3 次） | 5.5 s（首次 TTFB 3.2 s，CDN 尚未快取）、0.9 s、1.0 s | 未量測：我的測試瀏覽器沒有登入 claude.ai，無法開啟私人 Artifact |
| 執行時 FPS（同一台機器） | 60 fps | 應該相同（同一份程式），待實測 |

**怎麼比較**：分別用兩個網址開啟，按 F12 →「網路」勾選「停用快取」後重新整理，看「完成」時間與傳輸量；進入園區後，開發者主控台輸入 `__pg.stats.fps` 可以看到即時 FPS，`__pg.loadMs` 是場景載入耗時。也可以執行 `node web/tests/live-load.mjs <網址>`（需要能開啟該網址的瀏覽器狀態）。
