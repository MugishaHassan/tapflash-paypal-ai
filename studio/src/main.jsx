// TapFlash operators' analytics, built with AG Studio. The page gives the element and the data URL.
import React, { useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { AgStudioAiModule, createAiHarness } from 'ag-studio';
import { AgStudio, AgStudioProvider } from 'ag-studio-react';
import { geminiAdapter } from './gemini-adapter';
import { buildData } from './data';
import { initialReport } from './report';
import { tapflashTheme } from './theme';
import { widgets } from './widgets';
import './styles.css';

const SAVED = 'tapflash-studio-report-v2';

// Suggestions shown in a new conversation with the assistant.
const STARTERS = [
  { label: 'Refunds by restroom', prompt: 'Add a chart of refunded visits by restroom.' },
  { label: 'Which restroom needs a look?', prompt: 'Which restroom has the highest share of refunded visits, and how many visits is that?' },
  { label: 'Cleaning scores', prompt: 'Add a chart of the average Gemini cleaning score by restroom.' },
  { label: 'Summarise this page', prompt: 'Summarise what this page shows and what stands out.' },
];
const AI_MODULES = [AgStudioAiModule];

function load() {
  try { return JSON.parse(localStorage.getItem(SAVED)) || initialReport; } catch { return initialReport; }
}

function App({ url, currency, assistant, csrfToken }) {
  const [raw, setRaw] = useState(null);
  const [error, setError] = useState('');
  const [mode, setMode] = useState('view');
  const [api, setApi] = useState(null);
  const [initialState] = useState(load);
  const context = useMemo(() => ({ currency }), [currency]);
  // The assistant runs on Gemini through this site's own endpoint. Read once, when Studio is created.
  const ai = useMemo(() => {
    if (!assistant) return undefined;
    const adapter = geminiAdapter({ endpoint: assistant, csrfToken });
    return ({ api: studioApi }) => createAiHarness(studioApi, { adapter, promptStarters: STARTERS });
  }, [assistant, csrfToken]);

  useEffect(() => {
    const get = () => fetch(url, { credentials: 'same-origin' })
      .then((r) => { if (!r.ok) throw new Error(String(r.status)); return r.json(); })
      .then((json) => { setRaw(json); setError(''); })
      .catch(() => setError('The data could not be loaded. Check that you are signed in, then reload.'));
    get();
    const timer = setInterval(get, 60000);
    return () => clearInterval(timer);
  }, [url]);

  const data = useMemo(() => (raw ? buildData(raw, currency) : undefined), [raw, currency]);

  const save = () => { if (api) localStorage.setItem(SAVED, JSON.stringify(api.getState())); setMode('view'); };
  const reset = () => { localStorage.removeItem(SAVED); if (api) { api.setState(initialReport); api.clearHistory?.(); } setMode('view'); };

  if (error) return <p className="tf-studio-note tf-studio-error" role="alert">{error}</p>;
  if (!data) return <p className="tf-studio-note">Loading the data…</p>;

  return (
    <div className="tf-studio">
      <div className="tf-studio-bar">
        <p>Drag widgets to rearrange them, change any of them, or add your own. Click a bar or a restroom to filter the rest.</p>
        {mode === 'view'
          ? <button type="button" className="tf-btn tf-btn-main" onClick={() => setMode('edit')}>Edit this report</button>
          : <><button type="button" className="tf-btn" onClick={reset}>Reset to the default</button>
              <button type="button" className="tf-btn tf-btn-main" onClick={save}>Save</button></>}
      </div>
      <div className="tf-studio-canvas">
        <AgStudioProvider modules={ai ? AI_MODULES : undefined}>
          <AgStudio
            style={{ height: '100%', width: '100%' }}
            theme={tapflashTheme}
            layout={{ widgetBorderEnabled: true }}
            data={data}
            widgets={widgets}
            context={context}
            ai={ai}
            initialState={initialState}
            mode={mode}
            onApiReady={(event) => setApi(event.api)}
          />
        </AgStudioProvider>
      </div>
    </div>
  );
}

const root = document.getElementById('studio-root');
if (root) createRoot(root).render(<App url={root.dataset.url || '/ops/data'} currency={root.dataset.currency || 'USD'}
  assistant={root.dataset.assistant} csrfToken={root.dataset.csrf} />);
