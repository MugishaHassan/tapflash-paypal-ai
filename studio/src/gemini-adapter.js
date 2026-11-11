// A Gemini adapter for AG Studio's assistant. Studio hands over one turn (conversation, instructions,
// tool schemas); this turns it into a Gemini generateContent request, sends it to this site's own
// endpoint (which holds the key), and turns the answer back into Studio's output items and events.

let counter = 0;
const newId = (prefix) => `${prefix}_${Date.now().toString(36)}_${(counter += 1)}`;

// Gemini returns a signature with each function call and expects it back on the next turn.
const signatures = new Map();

function toContents(input) {
  const names = new Map();
  const contents = [];
  const push = (role, part) => {
    const last = contents[contents.length - 1];
    if (last && last.role === role) last.parts.push(part); else contents.push({ role, parts: [part] });
  };
  for (const item of input) {
    if (item.type === 'message') {
      const text = (item.content || []).map((c) => c.text || c.refusal || '').filter(Boolean).join('\n');
      if (text) push(item.role === 'assistant' ? 'model' : 'user', { text });
    } else if (item.type === 'function_call') {
      names.set(item.callId, item.name);
      let args = {};
      try { args = JSON.parse(item.arguments || '{}'); } catch { args = {}; }
      const part = { functionCall: { name: item.name, args } };
      // A call Gemini did not sign (for example one Studio added itself) carries the documented bypass value.
      const last = contents[contents.length - 1];
      const firstCallOfTurn = !(last && last.role === 'model' && last.parts.some((p) => p.functionCall));
      if (signatures.has(item.callId)) part.thoughtSignature = signatures.get(item.callId);
      else if (firstCallOfTurn) part.thoughtSignature = 'skip_thought_signature_validator';
      push('model', part);
    } else if (item.type === 'function_call_output') {
      push('user', { functionResponse: { name: names.get(item.callId) || 'tool', response: { result: item.output } } });
    }
  }
  return contents;
}

function toBody(request) {
  const body = { contents: toContents(request.input) };
  if (request.instructions) body.systemInstruction = { parts: [{ text: request.instructions }] };
  if (request.tools?.length && request.toolChoice !== 'none') {
    body.tools = [{ functionDeclarations: request.tools.map((t) => ({ name: t.name, description: t.description, parametersJsonSchema: t.parameters })) }];
    const choice = request.toolChoice;
    if (choice === 'required') body.toolConfig = { functionCallingConfig: { mode: 'ANY' } };
    else if (choice && typeof choice === 'object') body.toolConfig = { functionCallingConfig: { mode: 'ANY', allowedFunctionNames: [choice.name] } };
  }
  if (request.responseFormat?.type === 'json') {
    body.generationConfig = { responseMimeType: 'application/json', responseJsonSchema: request.responseFormat.schema };
  }
  return body;
}

function toOutput(answer) {
  const parts = answer?.candidates?.[0]?.content?.parts || [];
  const output = [];
  const text = parts.filter((p) => typeof p.text === 'string' && !p.thought).map((p) => p.text).join('');
  if (text) {
    output.push({ id: newId('msg'), kind: 'output', type: 'message', role: 'assistant', status: 'completed',
                  content: [{ type: 'text', text, annotations: [] }] });
  }
  for (const part of parts) {
    if (!part.functionCall) continue;
    const callId = newId('call');
    if (part.thoughtSignature) signatures.set(callId, part.thoughtSignature);
    output.push({ id: newId('fc'), kind: 'output', type: 'function_call', callId, name: part.functionCall.name,
                  arguments: JSON.stringify(part.functionCall.args || {}), status: 'completed' });
  }
  return output;
}

function* toEvents(output) {
  for (const item of output) {
    if (item.type === 'message') {
      yield { type: 'TEXT_MESSAGE_START', messageId: item.id, role: 'assistant' };
      yield { type: 'TEXT_MESSAGE_CONTENT', messageId: item.id, delta: item.content[0].text };
      yield { type: 'TEXT_MESSAGE_END', messageId: item.id };
    } else {
      yield { type: 'TOOL_CALL_START', toolCallId: item.callId, toolCallName: item.name };
      yield { type: 'TOOL_CALL_ARGS', toolCallId: item.callId, delta: item.arguments };
      yield { type: 'TOOL_CALL_END', toolCallId: item.callId };
    }
  }
}

export function geminiAdapter({ endpoint, csrfToken }) {
  return {
    executeTurn(request, options = {}) {
      const turn = (async () => {
        const createdAt = Date.now();
        const fail = (code, message) => ({ id: newId('resp'), createdAt, output: [], status: 'failed', error: { code, message } });
        let res;
        try {
          res = await fetch(endpoint, {
            method: 'POST', credentials: 'same-origin', signal: options.signal,
            headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken },
            body: JSON.stringify(toBody(request)),
          });
        } catch (error) {
          return fail(error?.name === 'AbortError' ? 'cancelled' : 'network', 'The assistant could not be reached.');
        }
        const answer = await res.json().catch(() => ({}));
        if (!res.ok) return fail(String(res.status), answer.error || 'The assistant is not available right now.');
        const output = toOutput(answer);
        if (!output.length) return fail('empty', 'The assistant gave no answer. Try asking in another way.');
        const usage = answer.usageMetadata || {};
        return { id: newId('resp'), createdAt, output, status: 'completed', model: answer.modelVersion,
                 usage: { inputTokens: usage.promptTokenCount || 0, outputTokens: usage.candidatesTokenCount || 0, totalTokens: usage.totalTokenCount } };
      })();
      return {
        stream: { async* [Symbol.asyncIterator]() { yield* toEvents((await turn).output); } },
        complete: turn,
      };
    },
  };
}
