# 松園別館 3D 互動導覽

花蓮松園別館（日治時期花蓮港陸軍兵事部，1942 年啟用）的 3D 互動導覽網站。可以第一人稱漫遊琉球松林與兩層拱廊建築，或跟著約 7 分鐘的自動導覽走完 14 個熱點。解說分成「史實／流傳說法／推測重建」三類，每條史實都附資料來源。

- 線上版（GitHub Pages）：https://triathlan226.github.io/pine-garden-3d/
- Claude Artifact 版（私人）：https://claude.ai/artifact/9sopdzQ8xUmi6xtxVDZrp9
- 離線展示：`dist.zip`（不在 repo 內，用 `npm run build` 後壓縮 `web/dist` 產生）
- 文件：[SPACE_PLAN.md](SPACE_PLAN.md)（照片分析、配置、尺寸）、[DECISIONS.md](DECISIONS.md)、[SOURCES.md](SOURCES.md)、[PERFORMANCE.md](PERFORMANCE.md)

## 目錄

```
blender/build_scene.py   程序化建模 + 烘焙 + 匯出（唯一的建模程式）
blender/layout.json      配置數據（建物位置、尺寸、步道、樹木參數）——改這裡就能改場景
blender/pine_garden.blend  建置結果（由腳本產生）
blender/textures_src/    CC0 材質（ambientCG）
blender/textures_gen/    自製松針／爬藤／灌木／草叢貼圖（tools/make_atlases.py）
content/hotspots.json    熱點文字（標題、摘要、解說、類別、來源、位置）
content/tour.json        自動導覽路線
content/sources.json     資料來源清單
tools/optimize.mjs       GLB 壓縮（Meshopt）＋貼圖轉 WebP
tools/make_plan.py       產生 docs/plan.svg 平面圖
web/                     Vite + Three.js 網站
renders/                 Blender 預覽圖與驗收截圖
```

## 本機執行

需要 Node.js 20 以上。

```bash
cd web
npm install
npm run dev
```

開啟 http://127.0.0.1:5173/ 。網址參數：`?q=low|medium|high` 強制畫質等級，`?nointro` 跳過開場，`?autostart` 直接進入。

正式版建置與預覽：

```bash
npm run build
npm run preview
```

## 修改熱點內容（不用改程式）

編輯 `content/hotspots.json`：

- `title`、`summary`（約 30 字）、`body`（150–250 字）
- `category`：只能是 `史實`、`流傳說法`、`推測重建`。標為史實的，`sources` 至少要有一個來源
- `sources`：對應 `content/sources.json` 的 `id`
- `plan`：熱點位置 `[X, Y, Z]`，單位公尺，使用 SPACE_PLAN.md 的平面座標（X 沿建物長軸向東北偏東，Y 朝建物背面，Z 是地面高度；二樓樓面是 4.45）
- `look`：觸發時視線看向的點

開發模式下存檔後重新整理就會看到。已經 build 好的網站，也可以直接改 `dist/content/hotspots.json`。

導覽路線在 `content/tour.json`：`points` 依序走，含 `stop` 的點會停下來開啟該熱點；`dwell` 可指定停留秒數。

## 重建 3D 模型

需要 Blender 5.1（其他 4.2+ 版本應該也能用）與 Python 3（含 Pillow、numpy）。

```bash
python tools/make_atlases.py
blender -b --factory-startup -P blender/build_scene.py -- --bake --render --export --samples 64
node tools/optimize.mjs
python tools/make_plan.py
```

- `--quick`：低解析度、少量樹木，約 1 分鐘，用來快速檢查幾何
- `--bake`：Cycles 烘焙光照圖與 AO（i7-1355U CPU 約 11 分鐘）
- `--render`：輸出 `renders/01–05_*.jpg` 預覽圖與小地圖底圖
- `--export`：輸出 `web/public/models/*_raw.glb`、`trees.json`、`scene_meta.json`

如果想用 Blender MCP 操作，在已開啟的 Blender 裡執行 `exec(open('blender/build_scene.py').read())` 也可以（不帶參數時只建置、存檔）。

## 測試

```bash
cd web
npx playwright install chromium
npm run build
npx playwright test
```

驗收截圖與數據輸出到 `renders/acceptance/`。

## 部署

推送到 `main` 後，GitHub Actions（`.github/workflows/deploy.yml`）會建置並發布到 GitHub Pages，base path 自動設成 `/<repo>/`。建置好的 `dist/` 用相對路徑，也可以直接放到任何靜態主機，或使用 `dist.zip` 離線展示（需要用本機伺服器開啟，例如 `npx serve dist`，因為瀏覽器不允許 `file://` 讀取模型）。

## Artifact 版

`node tools/build-artifact.mjs` 會輸出 `web/dist-artifact/`：主頁改成不含 `<html>` 外殼的片段，模型轉成內嵌的 `.gltf.json`。再用 Claude 的 Artifact 發佈功能上傳整個資料夾。

## 授權

程式碼：MIT。材質：ambientCG（CC0）。地圖資料：© OpenStreetMap contributors（ODbL），只用於建模。解說文字依公開資料整理改寫，來源列在 SOURCES.md。
