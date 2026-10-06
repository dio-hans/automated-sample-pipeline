"""R8 executive export. All KPIs come from R6; never recalculate ledgers."""
from io import BytesIO
from decimal import Decimal
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether

INK = colors.HexColor('#24322D')
OLIVE = colors.HexColor('#5D6E42')
LIGHT = colors.HexColor('#F3F5F0')
ZERO = Decimal('0')


def fmt(value, decimals=0):
    if value is None:
        return 'Not yet mapped'
    return f'{Decimal(str(value)):,.{decimals}f}'


def pdf_bytes(period, report):
    """Return PDF bytes. report is the dictionary from get_executive_report(period)."""
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=16*mm,
                            rightMargin=16*mm, topMargin=17*mm, bottomMargin=17*mm,
                            title=f'Nonda Executive Report - {period.label}', author='Nonda Commodities')
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name='NondaTitle', parent=styles['Title'], textColor=INK, fontSize=19, spaceAfter=8))
    styles.add(ParagraphStyle(name='NondaSection', parent=styles['Heading2'], textColor=OLIVE, spaceBefore=12, spaceAfter=6))
    styles.add(ParagraphStyle(name='NondaSmall', parent=styles['Normal'], fontSize=8, leading=11))
    story = [Paragraph('NONDA COMMODITIES', styles['NondaTitle']),
             Paragraph('Executive management report', styles['Heading2']),
             Paragraph(f'Report period: {escape(period.label)}', styles['Normal']),
             Spacer(1, 9*mm)]

    def section(title):
        story.append(Paragraph(escape(title), styles['NondaSection']))

    def p(text):
        story.append(Paragraph(escape(str(text)), styles['NondaSmall']))

    def grid(headers, rows, widths=None):
        if not rows:
            p('No records for the selected period.')
            return
        data = [[Paragraph(escape(str(x)), styles['NondaSmall']) for x in headers]]
        for row in rows:
            data.append([Paragraph(escape(str(x)), styles['NondaSmall']) for x in row])
        t = Table(data, colWidths=widths, repeatRows=1, hAlign='LEFT')
        t.setStyle(TableStyle([
            ('BACKGROUND',(0,0),(-1,0),LIGHT), ('TEXTCOLOR',(0,0),(-1,0),INK),
            ('VALIGN',(0,0),(-1,-1),'TOP'), ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#FAFBF8')]),
            ('LINEBELOW',(0,0),(-1,0),0.7,OLIVE), ('BOTTOMPADDING',(0,0),(-1,-1),7),
            ('TOPPADDING',(0,0),(-1,-1),7),
        ]))
        story.append(t)

    fin = report['executive_finance']
    ops = report['executive_operations']
    cons = report['executive_consumption']
    sales = report['sales_comparison']
    section('Management scorecard')
    grid(['Indicator','Value'], [
        ('Confirmed sales (UGX)',fmt(sales['current'])),
        ('Collections (UGX)',fmt(fin['collections_total'])),
        ('Expenses (UGX)',fmt(fin['expense_total'])),
        ('Current packaged receivables (UGX)',fmt(fin['outstanding_receivables'])),
        ('Coffee consumption (kg)',fmt(cons['total_consumed_kg'],2)),
        ('Processing yield (%)',fmt(ops['processing']['yield_pct'],1)),
        ('Packaging output (packs)',fmt(ops['packaging']['packs_produced'])),
        ('Open processing runs',ops['open_processing_count']),
        ('Open packaging runs',ops['open_packaging_count']),
        ('Reconciliation exceptions',ops['exception_count']),
    ], [88*mm, 88*mm])
    section('Comparison with previous equal-length period')
    if period.preset == 'overall':
        p('Not applicable to the overall reporting period.')
    else:
        grid(['Metric','Current','Previous','Change (%)'], [
            (name,fmt(report[key]['current']),fmt(report[key]['previous']),fmt(report[key]['percent'],1))
            for name,key in [('Sales','sales_comparison'),('Collections','collections_comparison'),
                             ('Expenses','expenses_comparison'),('Consumption (kg)','consumption_comparison')]
        ], [45*mm,45*mm,45*mm,41*mm])
    section('Collection methods')
    grid(['Method','UGX'],[(x['label'],fmt(x['amount'])) for x in fin['payment_methods']], [88*mm,88*mm])
    section('Expenditure by category')
    grid(['Category','UGX'],[(x['label'],fmt(x['total'])) for x in fin['expense_ranking']], [88*mm,88*mm])
    section('Current debt ageing (from release date)')
    grid(['Age','UGX'],[(x['label'],fmt(x['amount'])) for x in fin['ageing']], [88*mm,88*mm])
    section('Largest current debtors')
    grid(['Debtor','UGX'],[(x['name'],fmt(x['balance'])) for x in fin['debtors'][:10]], [88*mm,88*mm])
    section('Coffee consumption categories')
    grid(['Category','kg'],[(x['label'],fmt(x['kg'],2)) for x in cons['breakdown']], [88*mm,88*mm])
    section('Processing by operation')
    grid(['Operation','Input kg','Output kg','Loss kg','Yield %'],[
        (x['name'],fmt(x['input_kg'],2),fmt(x['primary_kg']+x['secondary_kg'],2),fmt(x['loss_kg'],2),fmt(x['yield_pct'],1))
        for x in ops['process_rows']
    ],[37*mm,36*mm,36*mm,36*mm,31*mm])
    section('Management alerts')
    for alert in report['executive_alerts']:
        p(f"{alert['title']}: {alert['detail']}")
    if not report['executive_alerts']:
        p('No current rule-based alerts.')
    section('Reporting notes')
    p('Receivables, debtor ageing and open runs are current snapshots, even for historical periods. Bulk receivables are excluded pending payment-linkage implementation. Release-date ageing is not contractual overdue ageing.')
    p('Collections use PackSettlement.cleared_at; the current auto_now field may change when a settlement is edited. No COGS, gross profit, net profit or margins are reported until R7 is implemented.')
    if sales['current'] is None:
        p('Sales KPI mapping is pending: the R3 total was not identified. Refer to the detailed Sales report.')

    def footer(canvas, document):
        canvas.saveState()
        canvas.setFont('Helvetica',8)
        canvas.setFillColor(colors.grey)
        canvas.drawString(16*mm, 11*mm, 'Nonda Commodities | Management report')
        canvas.drawRightString(194*mm, 11*mm, f'Page {document.page}')
        canvas.restoreState()
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
