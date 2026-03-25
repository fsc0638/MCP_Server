"""
PDF Helper — 預設支援中文的 PDF 生成器
==========================================
使用方式（在 mcp-python-executor 中）：

    import sys
    sys.path.insert(0, r'C:/Users/kicl1/OneDrive/文件/研發組專案/MCP_Server/workspace')
    from pdf_helper import ChinesePDF

    pdf = ChinesePDF()
    pdf.add_page()
    pdf.chapter_title('台北市松山區天氣報告')
    pdf.chapter_body('今日氣溫 19°C 至 22°C，陰天到多雲...')
    pdf.output('output.pdf')
"""

import os
from fpdf import FPDF
from fpdf.enums import XPos, YPos

# Windows 中文字體路徑（按優先順序嘗試）
_FONT_CANDIDATES = [
    (r'C:/Windows/Fonts/msjh.ttc', '微軟正黑體'),
    (r'C:/Windows/Fonts/mingliu.ttc', '細明體'),
    (r'C:/Windows/Fonts/simsun.ttc', '新宋體'),
]


class ChinesePDF(FPDF):
    """
    繼承 FPDF，自動載入中文字體。
    所有文字輸出自動使用中文字體，不會出現亂碼。
    """

    def __init__(self, orientation='portrait', unit='mm', format='A4'):
        super().__init__(orientation=orientation, unit=unit, format=format)
        self._chinese_font_name = None
        self._load_chinese_font()
        # 預設邊距
        self.set_auto_page_break(auto=True, margin=15)

    def _load_chinese_font(self):
        """嘗試載入可用的中文字體"""
        for font_path, font_label in _FONT_CANDIDATES:
            if os.path.exists(font_path):
                try:
                    self.add_font('ChineseFont', '', font_path)
                    self.add_font('ChineseFont', 'B', font_path)  # Bold variant
                    self._chinese_font_name = 'ChineseFont'
                    return
                except Exception:
                    continue
        # Fallback: 如果沒有中文字體，使用 Helvetica（會有亂碼風險）
        self._chinese_font_name = 'Helvetica'

    def set_chinese_font(self, size=12, bold=False):
        """設定中文字體"""
        style = 'B' if bold else ''
        self.set_font(self._chinese_font_name, style, size)

    def chapter_title(self, title, size=16):
        """寫入章節標題（粗體、置中）"""
        self.set_chinese_font(size=size, bold=True)
        self.cell(0, 12, text=title, new_x=XPos.LMARGIN, new_y=YPos.NEXT, align='C')
        self.ln(4)

    def chapter_subtitle(self, subtitle, size=13):
        """寫入子標題"""
        self.set_chinese_font(size=size, bold=True)
        self.cell(0, 10, text=subtitle, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(2)

    def chapter_body(self, body, size=11):
        """寫入內文段落（自動換行）"""
        self.set_chinese_font(size=size)
        self.multi_cell(0, 7, text=body)
        self.ln(3)

    def add_bullet(self, text, size=11):
        """寫入項目符號列表項"""
        self.set_chinese_font(size=size)
        self.cell(8, 7, text='•')
        self.multi_cell(0, 7, text=text)
        self.ln(1)

    def add_separator(self):
        """加入分隔線"""
        self.ln(3)
        y = self.get_y()
        self.line(self.l_margin, y, self.w - self.r_margin, y)
        self.ln(5)

    def header(self):
        """頁首（可覆寫）"""
        pass  # 預設無頁首

    def footer(self):
        """頁尾：顯示頁碼"""
        self.set_y(-15)
        self.set_chinese_font(size=8)
        self.cell(0, 10, text=f'第 {self.page_no()} 頁', align='C')
