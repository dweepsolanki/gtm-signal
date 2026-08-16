const $ = s => document.querySelector(s);
const esc = v => String(v || "—").replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let loadingTimers = [];

function setLoading(on) {
  loadingTimers.forEach(clearTimeout); loadingTimers = [];
  $('#loading').classList.toggle('hidden', !on);
  if (!on) return;
  const steps = [...document.querySelectorAll('.load-steps span')];
  steps.forEach(step => step.classList.remove('done'));
  steps.forEach((step, i) => loadingTimers.push(setTimeout(() => step.classList.add('done'), i * 360)));
}

$('#form').onsubmit = async e => {
  e.preventDefault(); const body = Object.fromEntries(new FormData(e.target));
  setLoading(true); $('#error').classList.add('hidden'); $('#results').classList.add('hidden');
  try { const r = await fetch('/api/analyze-account', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}); const data = await r.json(); if (!r.ok) throw Error(data.error || 'Analysis failed.'); render(data); }
  catch (err) { $('#error').innerHTML = `${esc(err.message)} <button type="button" id="retry">Try again</button>`; $('#error').classList.remove('hidden'); $('#retry').onclick = () => $('#form').requestSubmit(); }
  finally { setLoading(false); }
};

function domain(url) { try { return new URL(url).hostname.replace('www.',''); } catch { return 'Public source'; } }
function render(d) {
  $('#companyName').textContent = d.company.name; $('#companyDescription').textContent = d.company.description;
  $('#snapshot').innerHTML = Object.entries(d.company).filter(([k]) => ['name','description'].includes(k)).map(([k,v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join('');
  $('#signals').innerHTML = d.signals.map(s => `<div class="signal"><span>${esc(s.type)}</span><b>${esc(s.title)}</b><p>${esc(s.description)}</p><small>Evidence below ↓</small></div>`).join('');
  $('#evidence').innerHTML = d.evidence.map(x => `<article class="evidence-item"><div><span>${esc(x.signal_type || x.signal_id)}</span><em>${esc(x.source_type || 'public')} source</em></div><p>“${esc(x.source_text)}”</p><a href="${esc(x.source_url)}" target="_blank" rel="noreferrer">${esc(domain(x.source_url))} <b>↗</b></a></article>`).join('');
  $('#confidence').textContent = `${d.pain_hypothesis.confidence}%`; $('#hypothesisTitle').textContent = d.pain_hypothesis.title; $('#hypothesisDesc').textContent = d.pain_hypothesis.description; $('#reasoning').textContent = d.pain_hypothesis.reasoning; $('#angle').textContent = d.recommended_angle;
  $('#email').textContent = d.outreach.email; $('#linkedin').textContent = d.outreach.linkedin; $('#roi').textContent = d.roi_hook.statement;
  $('#assumptions').innerHTML = (Array.isArray(d.roi_hook.assumptions) ? d.roi_hook.assumptions : []).map(a => `<li>${esc(a)}</li>`).join('');
  $('#results').classList.remove('hidden'); $('#results').scrollIntoView({behavior:'smooth', block:'start'});
}

document.addEventListener('click', async e => {
  const button = e.target.closest('.copy'); if (!button) return;
  const text = $(`#${button.dataset.copy}`).textContent;
  try { await navigator.clipboard.writeText(text); button.textContent = 'Copied ✓'; setTimeout(() => button.textContent = 'Copy', 1600); } catch { button.textContent = 'Copy unavailable'; setTimeout(() => button.textContent = 'Copy', 1600); }
});
