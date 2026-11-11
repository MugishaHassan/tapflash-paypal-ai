// Three widgets only TapFlash has. Each asks Studio for its data with widgetApi.getData, so page
// filters and cross-filters from the other widgets apply to them too.
import React, { useEffect, useState } from 'react';
import { createWidgets } from 'ag-studio-react';

const money = (n, currency) => `${currency} ${(Number(n) || 0).toFixed(2)}`;

// Runs a flat query for the fields mapped to this widget and returns rows keyed by the names given.
// `plan` lists [name, mapping key, index within that mapping].
function useRows(widgetApi, dataMapping, plan, queryId) {
  const [rows, setRows] = useState(null);
  const picked = plan.map(([name, key, i]) => [name, (dataMapping?.[key] || [])[i]]);
  const ready = picked.every(([, field]) => field);
  const signature = JSON.stringify(picked.map(([, field]) => field && [field.fieldId || field.id, field.aggregation]));
  useEffect(() => {
    if (!ready) { widgetApi.setDisplayState('incompleteDataMapping'); return undefined; }
    let live = true;
    widgetApi.setDisplayState('loading', { prominent: rows === null });
    widgetApi.getData({ kind: 'flat', fields: picked.map(([, field]) => field) }, { queryId })
      .then((response) => {
        if (!live || response.replaced) return;
        const out = (response.results?.rows || []).map((row) => Object.fromEntries(picked.map(([name, field], i) =>
          [name, Array.isArray(row) ? row[i] : row[field.key] ?? row[field.fieldId]])));
        setRows(out);
        widgetApi.setDisplayState(out.length ? 'displayed' : 'noData');
      })
      .catch(() => { if (live) { setRows([]); widgetApi.setDisplayState('noData'); } });
    return () => { live = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [widgetApi, signature, queryId, ready]);
  return rows || [];
}

// 1. Money in motion: where every visitor's payment is, in PayPal's own steps.
const STEPS = [
  ['Held by PayPal', 'Held', 'Authorized. Nothing is taken yet.'],
  ['Taken at the door', 'Taken at the door', 'Captured when the attendant scanned the code.'],
  ['Paid', 'Paid', 'Paid in full at checkout.'],
  ['Refunded or released', 'Given back', 'Hold released or payment refunded.'],
];
function MoneyInMotion({ widgetApi, dataMapping, context }) {
  const rows = useRows(widgetApi, dataMapping, [['state', 'state', 0], ['visits', 'values', 0], ['amount', 'values', 1], ['back', 'values', 2]], 'motion');
  const by = Object.fromEntries(rows.map((r) => [r.state, r]));
  const total = rows.reduce((n, r) => n + (Number(r.visits) || 0), 0) || 1;
  return (
    <div className="tf-motion">
      {STEPS.map(([key, label, hint], i) => {
        const row = by[key] || {};
        const visits = Number(row.visits) || 0;
        const value = key === 'Refunded or released' ? row.back : row.amount;
        return (
          <div className={`tf-step tf-step-${i}`} key={key}>
            <div className="tf-step-label">{label}</div>
            <div className="tf-step-value">{money(value, context.currency)}</div>
            <div className="tf-step-bar"><span style={{ width: `${Math.round((visits / total) * 100)}%` }} /></div>
            <div className="tf-step-meta">{visits} {visits === 1 ? 'visit' : 'visits'}</div>
            <div className="tf-step-hint">{hint}</div>
          </div>
        );
      })}
    </div>
  );
}

// 2. Proof of clean: the latest cleaning photos, Gemini's score and whether PayPal paid the bonus.
const TONE = { approved: 'good', rejected: 'bad', review: 'warn' };
function ProofOfClean({ widgetApi, dataMapping, context }) {
  const names = ['id', 'time', 'photo', 'restroom', 'cleaner', 'score', 'decision', 'by', 'bonus', 'note'];
  const rows = useRows(widgetApi, dataMapping, names.map((name, i) => [name, 'cols', i]), 'proof');
  const latest = [...rows].sort((a, b) => String(b.time).localeCompare(String(a.time))).slice(0, 8);
  return (
    <div className="tf-proof">
      {latest.map((c) => (
        <figure className="tf-card" key={c.id} title={c.note || ''}>
          <img src={c.photo} alt={`Cleaning photo, ${c.restroom}`} loading="lazy" />
          <span className={`tf-score tf-${TONE[c.decision] || 'warn'}`}>{c.score ? `${c.score}/5` : 'No score'}</span>
          <figcaption>
            <strong>{c.restroom}</strong>
            <span>{c.cleaner}{c.by ? `, decided by ${c.by}` : ''}</span>
            <span className="tf-bonus">{Number(c.bonus) > 0 ? `Bonus ${money(c.bonus, context.currency)} paid` : c.decision === 'review' ? 'Waiting for a supervisor' : 'No bonus'}</span>
          </figcaption>
        </figure>
      ))}
    </div>
  );
}

// 3. Refund watch: flags a restroom when too many of its paid visits end in a refund.
function RefundWatch({ widgetApi, dataMapping, format }) {
  const limit = Number(format?.style?.alertPercent) || 25;
  const rows = useRows(widgetApi, dataMapping, [['restroom', 'restroom', 0], ['visits', 'values', 0], ['refunds', 'values', 1]], 'watch');
  const list = rows.map((r) => ({ ...r, rate: r.visits ? Math.round((r.refunds / r.visits) * 100) : 0 }))
    .sort((a, b) => b.rate - a.rate);
  const flagged = list.filter((r) => r.rate >= limit && r.refunds >= 2);
  return (
    <div className="tf-watch">
      <p className={`tf-watch-head ${flagged.length ? 'tf-bad' : 'tf-good'}`}>
        {flagged.length ? `${flagged.length} ${flagged.length === 1 ? 'restroom needs' : 'restrooms need'} a look` : 'No restroom is over the limit'}
        <small>Flag at {limit}% of visits refunded</small>
      </p>
      <ul>
        {list.map((r) => (
          <li key={r.restroom} className={r.rate >= limit && r.refunds >= 2 ? 'tf-flag' : ''}>
            <span className="tf-watch-name">{r.restroom}</span>
            <span className="tf-watch-bar"><span style={{ width: `${Math.min(r.rate, 100)}%` }} /></span>
            <span className="tf-watch-rate">{r.rate}%</span>
            <span className="tf-watch-n">{r.refunds} of {r.visits}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

const icon = (path) => `<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${path}</svg>`;

export const widgets = createWidgets({
  additionalTypes: [
    {
      id: 'tfMoneyInMotion', label: 'Money in motion', comp: MoneyInMotion,
      icon: icon('<path d="M3 12h14"/><path d="m13 6 6 6-6 6"/><circle cx="5" cy="12" r="1"/>'),
      dataMapping: {
        state: { type: 'field', supportedRoles: ['category'], required: true, intent: 'category', aiDescription: 'The money state of a payment: payments.state.' },
        values: { type: 'fieldset', supportedRoles: ['numeric'], required: true, intent: 'value', aiDescription: 'Three sums in order: visits, amount taken, amount given back.' },
      },
      form: (params) => params.createDefaults({ dataMappingItems: [{ key: 'state', label: 'Money state' }, { key: 'values', label: 'Visits, taken, given back' }] }),
      defaultSize: { width: 900, height: 220 }, minSize: { width: 420, height: 180 },
      ai: { description: 'Shows visitor payments by PayPal state: held, taken at the door, paid, given back.',
            usage: 'Use for a summary of where visitor money is.' },
    },
    {
      id: 'tfProofOfClean', label: 'Proof of clean', comp: ProofOfClean,
      icon: icon('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="m8 13 3 3 5-6"/>'),
      dataMapping: {
        cols: { type: 'fieldset', supportedRoles: ['category', 'temporal', 'numeric'], required: true, aiDescription: 'Cleaning check fields in order: id, time, photo, restroom, cleaner, score, decision, decided by, bonus, note.' },
      },
      form: (params) => params.createDefaults({ dataMappingItems: [{ key: 'cols', label: 'Cleaning check fields' }] }),
      defaultSize: { width: 900, height: 360 }, minSize: { width: 320, height: 240 },
      ai: { description: 'A wall of the latest cleaning photos with Gemini\'s score and whether the PayPal bonus was paid.',
            usage: 'Use to show cleaning evidence.' },
    },
    {
      id: 'tfRefundWatch', label: 'Refund watch', comp: RefundWatch,
      dataMapping: {
        restroom: { type: 'field', supportedRoles: ['category'], required: true, intent: 'category' },
        values: { type: 'fieldset', supportedRoles: ['numeric'], required: true, intent: 'value', aiDescription: 'Two sums in order: visits, refunded visits.' },
      },
      icon: icon('<path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4"/><circle cx="12" cy="17" r=".6"/>'),
      form: (params) => {
        const form = params.createDefaults({ dataMappingItems: [{ key: 'restroom', label: 'Restroom' }, { key: 'values', label: 'Visits, refunded visits' }] });
        form.items[1].items.push({ type: 'number', id: 'format.style.alertPercent', label: 'Flag at % refunded', defaultValue: 25 });
        return form;
      },
      defaultSize: { width: 480, height: 320 }, minSize: { width: 300, height: 220 },
      ai: { description: 'Lists restrooms by the share of paid visits that were refunded and flags any above a limit.',
            usage: 'Use to spot a restroom with a refund spike. Set format.style.alertPercent to change the limit.' },
    },
  ],
  menu: [{ label: 'TapFlash', widgetIds: ['tfMoneyInMotion', 'tfProofOfClean', 'tfRefundWatch'] }],
});
