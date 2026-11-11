// Turns the dashboard's JSON (/ops/data) into AG Studio data sources.
// One table per kind of money movement, joined on the restroom name.

const day = (iso) => (iso ? iso.slice(0, 10) : null);

// PayPal's own words for where a visitor's money is.
function moneyState(row) {
  if (row.status === 'held') return 'Held by PayPal';
  if (row.status === 'refunded' || row.refunded > 0) return 'Refunded or released';
  if (row.status === 'succeeded') return row.entered ? 'Taken at the door' : 'Paid';
  return 'Not completed';
}

const f = (specs) => specs.map(([id, name, format, hide]) => ({ id, name, format, ...(hide && { hide }) }));

export function buildData(raw, currency) {
  const payments = raw.payments.map((p) => ({
    id: String(p.id), date: day(p.time), time: p.time, restroom: p.restroom, method: p.method,
    state: moneyState(p), amount: p.amount, refunded: p.refunded, visits: 1,
    refundedVisits: p.refunded > 0 || p.status === 'refunded' ? 1 : 0, entered: p.entered ? 1 : 0,
  }));
  const cleaning = raw.cleaning.map((c) => ({
    id: String(c.id), date: day(c.time), time: c.time, restroom: c.restroom, cleaner: c.cleaner,
    decision: c.status, decidedBy: c.decidedBy || '', score: c.score, note: c.note || '', photo: c.photo,
    bonus: c.payout ? c.bonus : 0, bonusState: c.payout || 'Not paid', checks: 1,
  }));
  const payouts = raw.payouts.map((p) => ({
    id: String(p.id), date: day(p.time), time: p.time, restroom: p.restroom, kind: p.kind,
    amount: p.amount, status: p.status, batch: p.batch || '', visits: p.visits,
  }));
  const reports = raw.reports.map((r) => ({
    id: String(r.id), date: day(r.time), restroom: r.restroom, message: r.message, status: r.status,
    refundBy: r.refundBy || 'None', reports: 1,
  }));
  const restrooms = raw.restrooms.map((r) => ({ name: r.name, area: r.area || '', operator: r.operator || '', share: r.share, owed: r.owed }));

  return {
    description:
      `TapFlash operations. Amounts are ${currency} as charged by PayPal (sandbox). A payment is first held by PayPal, ` +
      'then taken when the attendant scans the visitor\'s code, or released or refunded if the visit fails. ' +
      'Cleaning checks are photos scored 1 to 5 by Gemini; an approved photo pays the cleaner a bonus with PayPal Payouts.',
    sources: [
      { id: 'restrooms', description: 'One row per restroom, with its operator and the share owed now.', data: restrooms,
        fields: f([['name', 'Restroom', 'textFormat'], ['area', 'Area', 'textFormat'], ['operator', 'Operator', 'textFormat'],
                   ['share', 'Operator share %', 'integerFormat'], ['owed', 'Owed to operator', 'currencyFormat']]) },
      { id: 'payments', description: 'One row per visitor payment. State says where the money is.', data: payments,
        fields: f([['id', 'Payment ID', 'textFormat', true], ['date', 'Day', 'dateFormat'], ['restroom', 'Restroom', 'textFormat'],
                   ['method', 'Paid with', 'textFormat'], ['state', 'Money state', 'textFormat'], ['amount', 'Taken', 'currencyFormat'],
                   ['refunded', 'Given back', 'currencyFormat'], ['visits', 'Visits', 'integerFormat'],
                   ['refundedVisits', 'Refunded visits', 'integerFormat'], ['entered', 'Let in at the door', 'integerFormat']]) },
      { id: 'cleaning', description: 'One row per cleaning photo checked by Gemini.', data: cleaning,
        fields: f([['id', 'Check ID', 'textFormat', true], ['date', 'Day', 'dateFormat'], ['time', 'Time', 'textFormat', true],
                   ['restroom', 'Restroom', 'textFormat'], ['cleaner', 'Cleaner', 'textFormat'], ['decision', 'Decision', 'textFormat'],
                   ['decidedBy', 'Decided by', 'textFormat'], ['score', 'Gemini score', 'integerFormat'], ['note', 'Gemini note', 'textFormat'],
                   ['photo', 'Photo', 'textFormat', true], ['bonus', 'Bonus paid', 'currencyFormat'], ['bonusState', 'Bonus state', 'textFormat'],
                   ['checks', 'Checks', 'integerFormat']]) },
      { id: 'payouts', description: 'PayPal Payouts sent to operators (their share) and cleaners (bonuses).', data: payouts,
        fields: f([['id', 'Payout ID', 'textFormat', true], ['date', 'Day', 'dateFormat'], ['restroom', 'Restroom', 'textFormat'],
                   ['kind', 'Paid for', 'textFormat'], ['amount', 'Paid out', 'currencyFormat'], ['status', 'PayPal status', 'textFormat'],
                   ['batch', 'PayPal batch', 'textFormat'], ['visits', 'Visits covered', 'integerFormat']]) },
      { id: 'reports', description: 'Problems visitors reported, and who decided the refund.', data: reports,
        fields: f([['id', 'Report ID', 'textFormat', true], ['date', 'Day', 'dateFormat'], ['restroom', 'Restroom', 'textFormat'],
                   ['message', 'What they said', 'textFormat'], ['status', 'Status', 'textFormat'], ['refundBy', 'Refund decided by', 'textFormat'],
                   ['reports', 'Reports', 'integerFormat']]) },
    ],
    relationships: ['payments', 'cleaning', 'payouts', 'reports'].map((t) => ({
      id: `${t}-restroom`, source: { tableId: t, fieldId: 'restroom' }, target: { tableId: 'restrooms', fieldId: 'name' }, type: 'many-to-one',
    })),
  };
}
