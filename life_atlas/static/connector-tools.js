// Sources view: the registered connectors, what each one is doing, and the
// switch that turns it off. Every connector is a separate service, so a
// connector that is missing or broken shows as that connector's state here and
// changes nothing else about Life Atlas.
let connectorData=null,connectorLoading=false,connectorProbe={},connectorOpen={};
const CONNECTOR_STATES={
  available:['ok','Available'],
  degraded:['warn','Degraded'],
  unavailable:['bad','Unavailable'],
  auth_required:['warn','Needs authorising'],
  incompatible:['bad','Incompatible'],
  disabled:['off','Switched off'],
  unknown:['off','Not checked yet']
};
const CONNECTOR_KINDS={
  source:'Feeds Life Atlas',
  consumer:'Reads from Life Atlas',
  bidirectional:'Feeds and reads'
};

function connectorState(c){return CONNECTOR_STATES[c.state]||CONNECTOR_STATES.unknown}
function connectorStamp(value){return value?String(value).replace('T',' ').replace('Z',' UTC'):'never'}

async function loadConnectors(refresh){
  if(connectorLoading)return;
  connectorLoading=true;
  // sources() starts the first load while render() is still running, so the
  // busy repaint is deferred rather than re-entering render from inside itself.
  if(view==='sources')setTimeout(()=>{if(connectorLoading&&view==='sources')render()},0);
  try{
    const response=refresh
      ?await fetch('/api/connectors/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'})
      :await fetch('/api/connectors');
    if(!response.ok)throw new Error(`Connector request failed (${response.status})`);
    connectorData=(await response.json()).connectors||[];
  }catch(error){
    console.error(error);
    connectorData=null;
    toast(error.message);
  }finally{
    connectorLoading=false;
    if(view==='sources')render();
  }
}

function sources(){
  if(connectorData===null){
    if(!connectorLoading)loadConnectors(false);
    return `<div class="connector-page"><p class="muted">Loading connectors…</p></div>`;
  }
  const rows=connectorData.map(connectorCard).join('');
  return `<div class="connector-page">
    <div class="entity-actions">
      <button class="primary" data-connector-action="new">＋ Add connector</button>
      <button data-connector-action="refresh"${connectorLoading?' disabled':''}>${connectorLoading?'Checking…':'↻ Check all'}</button>
    </div>
    <p class="muted">Each connector runs as its own app and owns its own archive. Life Atlas keeps only the curated record, so switching a connector off never removes evidence that was already promoted into it.</p>
    ${connectorData.length?`<div class="connector-list">${rows}</div>`:'<div class="diary-empty"><div><b>No connectors registered</b><p>Add one to let Life Atlas reach a source archive, or to let a client read the curated record.</p></div></div>'}
  </div>`;
}

function connectorCard(c){
  const [tone,label]=connectorState(c),id=c.connector_id,probe=connectorProbe[id];
  const direction=[c.reads_into_life_atlas?'<span class="pill">→ into Life Atlas</span>':'',
                   c.reads_from_life_atlas?'<span class="pill">← out of Life Atlas</span>':''].join('');
  const version=c.info&&c.info.connector_version?`v${esc(c.info.connector_version)}`:'version unknown';
  const upstream=c.info&&c.info.upstream_name
    ?`<div><span>Upstream</span><b>${esc(c.info.upstream_name)} ${esc(c.info.upstream_version||'')}</b></div>`:'';
  const capabilities=(c.capabilities||[]).map(x=>`<span class="pill">${esc(x)}</span>`).join('')||'<span class="muted">none advertised</span>';
  return `<article class="connector-card ${tone}" data-connector-id="${esc(id)}">
    <header>
      <div>
        <h3>${esc(c.name)}</h3>
        <div class="connector-sub">${esc(CONNECTOR_KINDS[c.kind]||c.kind)} · ${esc(id)}</div>
      </div>
      <label class="connector-switch" title="${c.enabled?'Switch off':'Switch on'}">
        <input type="checkbox" data-connector-action="toggle"${c.enabled?' checked':''}>
        <span></span>
      </label>
    </header>
    <div class="connector-state"><span class="state-dot"></span><b>${esc(label)}</b>${c.error?`<span class="connector-error">${esc(c.error)}</span>`:''}</div>
    <div class="connector-facts">
      <div><span>Direction</span><b>${direction||'<span class="muted">none</span>'}</b></div>
      <div><span>Address</span><b>${c.base_url?esc(c.base_url):'<span class="muted">calls in — no address</span>'}</b></div>
      <div><span>Connector key</span><b>${c.has_key?'stored':'<span class="muted">not set</span>'}</b></div>
      <div><span>Protocol</span><b>${c.info&&c.info.protocol_version?esc(c.info.protocol_version):'—'} · ${version}</b></div>
      ${upstream}
      <div><span>Last checked</span><b>${esc(connectorStamp(c.last_checked_at))}</b></div>
      <div><span>Last successful sync</span><b>${esc(connectorStamp(c.last_successful_sync))}</b></div>
    </div>
    <div class="connector-caps"><span>Capabilities</span>${capabilities}</div>
    ${c.notes?`<p class="connector-notes">${esc(c.notes)}</p>`:''}
    <div class="connector-actions">
      <button data-connector-action="probe">Check now</button>
      <button data-connector-action="edit">Configure</button>
      ${c.reads_into_life_atlas?'<button data-connector-action="inspect">Inspect archive</button>':''}
      <button class="danger" data-connector-action="remove">Remove</button>
    </div>
    ${connectorOpen[id]?`<form class="connector-search" data-connector-action="search">
      <input name="query" placeholder="Search this archive" aria-label="Search this connector's archive" value="${esc(connectorOpen[id]===true?'':connectorOpen[id])}">
      <button>Search</button>
    </form>`:''}
    ${probe?`<div class="connector-inspect">${probe}</div>`:''}
  </article>`;
}

document.addEventListener('click',async e=>{
  const button=e.target.closest('[data-connector-action]');
  if(!button||view!=='sources')return;
  const action=button.dataset.connectorAction;
  if(action==='refresh')return loadConnectors(true);
  if(action==='new')return openConnectorEditor(null);
  const card=button.closest('[data-connector-id]');
  if(!card)return;
  const id=card.dataset.connectorId,entry=(connectorData||[]).find(c=>c.connector_id===id);
  if(action==='toggle')return setConnectorEnabled(id,button.checked);
  if(action==='probe')return connectorAction(id,'probe','Checked');
  if(action==='edit')return openConnectorEditor(entry);
  if(action==='inspect'){connectorOpen[id]=connectorOpen[id]?false:true;return render()}
  if(action==='remove'){
    if(!window.confirm(`Remove ${entry?entry.name:id} from Life Atlas? The connector app itself and its own archive are untouched.`))return;
    const response=await fetch(`/api/connectors/${encodeURIComponent(id)}/delete`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    if(!response.ok)return toast((await response.json()).error||'Could not remove connector');
    delete connectorProbe[id];
    toast('Connector removed');
    return loadConnectors(false);
  }
});

async function setConnectorEnabled(id,enabled){
  const response=await fetch(`/api/connectors/${encodeURIComponent(id)}/${enabled?'enable':'disable'}`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  if(!response.ok){toast((await response.json()).error||'Could not change this connector');return loadConnectors(false)}
  toast(enabled?'Connector switched on':'Connector switched off');
  // Switching on says nothing about whether the service is reachable; ask it.
  if(enabled)return connectorAction(id,'probe',null);
  return loadConnectors(false);
}

async function connectorAction(id,action,message){
  const response=await fetch(`/api/connectors/${encodeURIComponent(id)}/${action}`,{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
  if(!response.ok)return toast((await response.json()).error||'Could not reach this connector');
  if(message)toast(message);
  return loadConnectors(false);
}

// Inspection is a read of the connector's own archive. Nothing seen here is
// written to the curated record; promotion stays a separate, explicit act.
document.addEventListener('submit',async e=>{
  const form=e.target.closest('form.connector-search');
  if(!form)return;
  e.preventDefault();
  const id=form.closest('[data-connector-id]').dataset.connectorId,query=form.elements.query.value;
  if(!query.trim())return;
  connectorOpen[id]=query;
  const response=await fetch(`/api/connectors/${encodeURIComponent(id)}/search`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({query})});
  const result=await response.json();
  if(!response.ok)return toast(result.error||'Could not search this connector');
  if(result.error)connectorProbe[id]=`<p class="connector-error">${esc(result.error)}</p>`;
  else if(!result.items.length)connectorProbe[id]=`<p class="muted">No source items matched “${esc(result.query)}”.</p>`;
  else connectorProbe[id]=`<h4>${result.items.length} source item(s) for “${esc(result.query)}”</h4>`+result.items.map(item=>
    `<div class="evidence"><b>${esc(item.title||item.item_type)}</b> · ${esc(item.timestamp||'undated')} · ${esc(item.lifecycle)}<p>${esc((item.text||'').slice(0,220))}</p></div>`).join('');
  render();
});

function openConnectorEditor(entry){
  const c=entry||{connector_id:'',name:'',kind:'source',base_url:'',notes:'',enabled:false,has_key:false};
  $('#connector-content').innerHTML=`<h2>${entry?'Configure connector':'Add connector'}</h2>
    <form id="connector-form">
      <input type="hidden" name="existing" value="${entry?'1':''}">
      <label>Identifier<input name="connector_id" required pattern="[A-Za-z0-9_-]{1,64}" value="${esc(c.connector_id)}"${entry?' readonly':''}></label>
      <label>Name<input name="name" required value="${esc(c.name)}"></label>
      <label>Direction<select name="kind">
        <option value="source">Feeds Life Atlas</option>
        <option value="consumer">Reads from Life Atlas</option>
        <option value="bidirectional">Feeds and reads</option>
      </select></label>
      <label>Connector Protocol address<input name="base_url" placeholder="http://local-life-atlas-whatsapp-archive:8097" value="${esc(c.base_url)}"></label>
      <label>Connector key${c.has_key?' <span class="muted">(a key is stored; leave blank to keep it)</span>':''}<input name="auth_key" type="password" autocomplete="off" placeholder="${c.has_key?'unchanged':'none'}"></label>
      <label>Notes<textarea name="notes">${esc(c.notes)}</textarea></label>
      <label class="inline"><input type="checkbox" name="enabled"${c.enabled?' checked':''}> Switched on</label>
      <button class="primary" type="submit">Save connector</button>
    </form>`;
  $('#connector-form').elements.kind.value=c.kind;
  $('#connector-form').onsubmit=saveConnector;
  $('#connector-dialog').showModal();
}

async function saveConnector(e){
  e.preventDefault();
  const fd=new FormData(e.target),body={
    connector_id:fd.get('connector_id'),
    name:fd.get('name'),
    kind:fd.get('kind'),
    base_url:fd.get('base_url'),
    notes:fd.get('notes'),
    enabled:fd.has('enabled')
  };
  // An unchanged password field must not blank a stored key, so it is sent only
  // when the person actually typed one, or when the connector is new.
  const key=String(fd.get('auth_key')||'');
  if(key||!fd.get('existing'))body.auth_key=key;
  const response=await fetch('/api/connectors',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const answer=await response.json();
  if(!response.ok)return toast(answer.error||'Could not save this connector');
  $('#connector-dialog').close();
  toast('Connector saved');
  if(body.enabled)return connectorAction(body.connector_id,'probe',null);
  return loadConnectors(false);
}

document.querySelector('.connector-close').onclick=()=>$('#connector-dialog').close();
