// The report an operator sees first. They can rearrange it, change any widget and add their own.
// Columns are 24 across; rows are short, so heights are given in units of two rows.
const at = (xTrack, yTrack, xSpan, ySpan) => ({ xTrack, yTrack: yTrack * 2, xSpan, ySpan: ySpan * 2 });
const FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';
const type = (fontSize, fontWeight = 'normal') => ({ fontFamily: FONT, fontSize, fontWeight, fontStyle: 'normal' });
const title = (text) => ({ title: { enabled: true, text, typography: type(14, 'bold') } });
const sum = (id) => ({ id, aggregation: 'sum' });
const kpi = (text, value) => ({ type: 'value', dataMapping: { value: [value] }, format: { ...title(text), style: { typography: type(26, 'bold') } } });

export const initialReport = {
  pages: [
    {
      id: 'operations',
      widgets: {
        restroom: { type: 'list-filter', dataMapping: { value: [{ id: 'restrooms.name' }] }, format: title('Restroom') },
        motion: { type: 'tfMoneyInMotion', dataMapping: { state: [{ id: 'payments.state' }], values: [sum('payments.visits'), sum('payments.amount'), sum('payments.refunded')] },
                  format: title('Money in motion: every visitor payment, by PayPal state') },
        taken: kpi('Taken from visitors', sum('payments.amount')),
        back: kpi('Given back', sum('payments.refunded')),
        out: kpi('Paid out with PayPal Payouts', sum('payouts.amount')),
        byRestroom: { type: 'bar-chart-stacked', dataMapping: { categoryKey: [{ id: 'payments.restroom' }], valueKey: [sum('payments.amount'), sum('payments.refunded')] },
                      format: title('Taken and given back, by restroom') },
        watch: { type: 'tfRefundWatch', dataMapping: { restroom: [{ id: 'payments.restroom' }], values: [sum('payments.visits'), sum('payments.refundedVisits')] },
                 format: { ...title('Refund watch'), style: { alertPercent: 25 } } },
        byDay: { type: 'line-chart', dataMapping: { categoryKey: [{ id: 'payments.date' }], valueKey: [sum('payments.visits')] }, format: title('Paid visits by day') },
        proof: { type: 'tfProofOfClean', dataMapping: { cols: ['id', 'time', 'photo', 'restroom', 'cleaner', 'score', 'decision', 'decidedBy', 'bonus', 'note'].map((f) => ({ id: `cleaning.${f}` })) },
                 format: title('Proof of clean: latest photos, Gemini\'s score, bonus') },
        decisions: { type: 'donut-chart', dataMapping: { categoryKey: [{ id: 'cleaning.decision' }], valueKey: [sum('cleaning.checks')] }, format: title('Cleaning decisions') },
        payouts: { type: 'grid', dataMapping: { cols: [{ id: 'payouts.date' }, { id: 'payouts.kind' }, { id: 'payouts.restroom' }, sum('payouts.amount'), { id: 'payouts.status' }, { id: 'payouts.batch' }] },
                   format: title('PayPal Payouts sent') },
      },
      widgetLayout: {
        restroom: at(0, 0, 5, 9), motion: at(5, 0, 19, 5),
        taken: at(5, 5, 6, 4), back: at(11, 5, 6, 4), out: at(17, 5, 7, 4),
        byDay: at(0, 9, 5, 9), byRestroom: at(5, 9, 11, 9), watch: at(16, 9, 8, 9),
        proof: at(0, 18, 16, 11), decisions: at(16, 18, 8, 11),
        payouts: at(0, 29, 24, 9),
      },
    },
  ],
  selectedPageId: 'operations',
  panels: { filters: { collapsed: true }, data: { collapsed: true } },
};
