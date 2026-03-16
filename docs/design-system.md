# K WAY AgentPortal — 設計規範

> **版本：** 2.0 · **風格：** UIUXPRO Claymorphism + B2B SaaS Clean
> **適用頁面：** 全站（以 Chat 頁為基準）

---

## 1. 設計風格

| 屬性 | 規格 |
|------|------|
| **Style** | Claymorphism + Bento Box Grid |
| **色調** | 冷灰藍 Light（B2B Clean） |
| **字體** | Plus Jakarta Sans |
| **圓角** | 大圓角（14–20px），統一、圓潤 |
| **陰影** | Clay 雙層陰影（outer + inset highlight） |
| **動效** | 200ms ease-out，hover 微上浮 `translateY(-1~3px)` |

---

## 2. 色彩系統

### 2.1 背景層次

```css
--bg-base:     #F1F4F9   /* 頁面底層背景（冷灰藍） */
--bg-surface:  #FFFFFF   /* 主內容區背景 */
--bg-card:     #FFFFFF   /* 卡片背景 */
--bg-sidebar:  #E8ECF3   /* 側邊欄背景（較深一階） */
--bg-hover:    #EDF0F7   /* 懸停狀態 */
--bg-active:   #E0E8FF   /* 選中/啟用狀態 */
--bg-input:    #FFFFFF   /* 輸入框背景 */
```

### 2.2 品牌色

```css
--kway-blue:         #18409b   /* 主品牌藍（凱衛深邃藍） */
--kway-blue-dark:    #0f2d72   /* hover 深化 */
--kway-blue-mid:     #2355b8   /* 中間層 */
--kway-blue-light:   #dce8ff   /* 淺藍（邊框/填充） */
--kway-blue-pale:    #EEF3FC   /* 極淺藍（背景填充） */

--kway-orange:       #f68300   /* 主品牌橘（凱衛活力橘） */
--kway-orange-dark:  #d97000   /* hover 深化 */
--kway-orange-light: #FFF0D6   /* 極淺橘（背景填充） */
```

### 2.3 Accent 輔助色

```css
--accent-teal:       #0EA5A0   /* 青綠（在線狀態、轉錄、強調） */
--accent-teal-pale:  #E6F9F8   /* 極淺青（背景填充） */
--accent-coral:      #F05252   /* 珊瑚紅（危險/刪除） */
--accent-yellow:     #F59E0B   /* 琥珀黃（警告/提示） */
--accent-violet:     #7C3AED   /* 紫羅蘭（標籤/徽章） */
```

### 2.4 文字色

```css
--text-primary:    #0F172A   /* 主要文字（深海軍藍，高對比） */
--text-secondary:  #475569   /* 次要文字（Slate-600） */
--text-muted:      #94A3B8   /* 輔助文字（Slate-400） */
--text-inverse:    #FFFFFF   /* 反色文字（用於深色背景） */
```

### 2.5 邊框

```css
--border-subtle:   #E2E8F0   /* 細邊框（卡片、分隔） */
--border-medium:   #CBD5E1   /* 中邊框（輸入框）*/
--border-focus:    #18409b   /* 聚焦邊框 */
```

---

## 3. 字體規範

**字體家族**
```css
font-family: 'Plus Jakarta Sans', 'Noto Sans TC', system-ui, -apple-system, sans-serif;
```

**Google Fonts 引入**
```html
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
```
```css
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:ital,wght@0,400;0,500;0,600;0,700;0,800;1,400&display=swap');
```

**字重用途**

| Weight | 用途 |
|--------|------|
| `400` Regular | 內文、描述文字 |
| `500` Medium | 標籤、次要 UI 文字 |
| `600` SemiBold | 卡片標題、按鈕 |
| `700` Bold | 頁面標題、側邊欄 active |
| `800` ExtraBold | 主標題、Dashboard heading、數字顯示 |

**字型尺寸**

| 層級 | 大小 | Weight | Letter Spacing |
|------|------|--------|---------------|
| Display | `1.65rem` | 800 | `-0.030em` |
| Heading | `1.10rem` | 700 | `-0.020em` |
| Title | `0.92rem` | 700 | — |
| Card Title | `0.82–0.86rem` | 700 | — |
| Body | `0.88–0.90rem` | 400–500 | — |
| Caption | `0.73–0.78rem` | 500–600 | — |
| Label | `0.68–0.70rem` | 800 | `+0.08em` uppercase |
| Micro | `0.69rem` | 500 | — |

---

## 4. 圓角系統

```css
--radius-xs:   6px    /* 微型元素（小 badge） */
--radius-sm:   10px   /* 按鈕、小卡片、圖示 */
--radius-md:   14px   /* 標準卡片、輸入框 */
--radius-lg:   20px   /* 主要卡片、大容器 */
--radius-xl:   26px   /* Hero 卡片、Modal */
--radius-full: 9999px /* 膠囊形（Tag、Avatar、進度條）*/
```

---

## 5. 陰影系統（Claymorphism）

Clay 陰影的特徵：**外層投影 + inset 頂部高光**，製造輕微 3D 浮起感。

```css
--shadow-clay-sm: 0 2px 8px rgba(0,0,0,0.08),
                  inset 0 1px 0 rgba(255,255,255,0.80);

--shadow-clay-md: 0 6px 20px rgba(0,0,0,0.10),
                  inset 0 1px 0 rgba(255,255,255,0.70);

--shadow-clay-lg: 0 12px 32px rgba(0,0,0,0.12),
                  inset 0 1px 0 rgba(255,255,255,0.60);

/* 品牌色投影 */
--shadow-blue:    0 4px 16px rgba(24,64,155,0.25);
--shadow-orange:  0 4px 16px rgba(246,131,0,0.28);
```

**使用規則**
- 預設元素（卡片、按鈕）→ `shadow-clay-sm`
- hover 狀態 → `shadow-clay-md`
- 選取/展開的卡片 → `shadow-clay-lg`

---

## 6. 動效規範

```css
--transition: 0.18s cubic-bezier(0.4, 0, 0.2, 1);
```

| 情境 | 效果 |
|------|------|
| 卡片 hover | `translateY(-2~3px)` + shadow-clay-md |
| 按鈕 hover | `translateY(-1px)` + shadow 加深 |
| 按鈕 active（按下）| `translateY(0)` + shadow 回 sm |
| Send 按鈕 hover | `scale(1.07) translateY(-1px)` |
| 訊息出現 | `translateY(10px) + scale(0.97)` → `translateY(0) scale(1)`（`cubic-bezier(0.34, 1.56, 0.64, 1)` bounce） |
| toast 出現 | `translateY(10px)` → `translateY(0)`（same bounce easing） |

---

## 7. 元件規格

### Topbar
```
高度：58px
背景：#FFFFFF
底部邊框：1.5px solid #E2E8F0
陰影：0 2px 12px rgba(0,0,0,0.05)
```

### Sidebar
```
寬度：240px（左）/ 276px（右）
背景：#E8ECF3
邊框：1.5px solid #E2E8F0
```

### 卡片（Card）
```
背景：#FFFFFF
邊框：1.5px solid #E2E8F0
圓角：14–20px
陰影：shadow-clay-sm
hover：translateY(-2px) + shadow-clay-md
```

### 輸入框
```
背景：#FFFFFF
邊框：1.5px solid #CBD5E1
圓角：20px（大輸入框）/ 14px（小）
focus：border-color #18409b + box-shadow 0 0 0 3px rgba(24,64,155,0.07)
```

### 按鈕

| 類型 | 背景 | 文字 | 邊框 |
|------|------|------|------|
| Primary | `#18409b` | `#FFFFFF` | none |
| Secondary | `#FFFFFF` | `#0F172A` | `1.5px #E2E8F0` |
| Ghost | `transparent` | `#475569` | none |
| Danger | `#F05252` | `#FFFFFF` | none |
| Teal Accent | `#0EA5A0` | `#FFFFFF` | none |

### Badge / Tag
```
padding：3px 9px
border-radius：9999px（膠囊）
font-weight：700
font-size：0.70rem
box-shadow：shadow-clay-sm
```

| 類型 | 背景 | 文字 | 邊框 |
|------|------|------|------|
| Blue | `#EEF3FC` | `#18409b` | `rgba(24,64,155,0.15)` |
| Green/Teal | `#E6F9F8` | `#1A8A83` | `rgba(75,191,181,0.25)` |
| Orange | `#FFF0D6` | `#d97000` | `rgba(246,131,0,0.20)` |
| Red | `#FEE2E2` | `#F05252` | `rgba(240,82,82,0.20)` |

### Avatar
```
border-radius：9999px
border：2.5px solid rgba(255,255,255,0.90)
box-shadow：shadow-clay-sm

gradient（預設）：linear-gradient(135deg, #0EA5A0, #18409b)
```

### 進度條
```
高度：6px
背景：rgba(0,0,0,0.07)
border-radius：9999px

Blue fill：linear-gradient(90deg, #18409b, #0EA5A0)
Orange fill：linear-gradient(90deg, #f68300, #F05252)
```

### Scrollbar
```css
::-webkit-scrollbar       { width: 4–5px; }
::-webkit-scrollbar-thumb { background: rgba(0,0,0,0.10); border-radius: 4px; }
```

---

## 8. 佈局規格

### Chat 頁三欄佈局
```
Grid：240px | 1fr | 276px
Topbar 高度：58px
左側邊欄標題區：padding 16px 14px 10px
訊息區 padding：24px 28px
輸入區 padding：14px 24px 18px
```

### 響應式斷點
```
≤ 1100px：隱藏右側欄（276px → 0）
≤ 768px：隱藏左側欄（240px → 0）
```

---

## 9. 無障礙規範（WCAG AA）

- 正文對比度：`#0F172A` on `#FFFFFF` → **18.1:1** ✓
- 次要文字：`#475569` on `#FFFFFF` → **7.0:1** ✓
- Brand Blue on White：`#18409b` → **8.5:1** ✓
- 所有可點擊元素需有 `cursor: pointer`
- 所有 hover 需有 150–200ms smooth transition
- 支援 `prefers-reduced-motion`：動畫縮短至 `0.01ms`
- Icon 專用按鈕必須有 `aria-label`
- 不使用 emoji 作為功能性圖示，一律改用 SVG（Lucide / Heroicons）

---

## 10. 快速套用 Checklist

套用到新頁面時，確認以下項目：

```
[ ] @import Plus Jakarta Sans
[ ] <link rel="preconnect"> fonts.googleapis.com / fonts.gstatic.com
[ ] :root 或頁面 root element 套用所有 CSS token
[ ] 所有卡片使用 border-radius 14–20px + shadow-clay-sm
[ ] hover 加上 translateY(-2px) + shadow-clay-md
[ ] 圖示全用 SVG，不用 emoji
[ ] aria-label 補齊所有 icon button
[ ] 測試 375px / 768px / 1024px / 1440px 四個斷點
[ ] 確認 prefers-reduced-motion 有覆蓋
```
