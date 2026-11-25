import QRCode from 'qrcode';

const $ = selector => document.querySelector(selector);
const welcome = $('#welcome');
const loading = $('#loading');
const sessionPanel = $('#session');
const errorPanel = $('#error');
const uploadForm = $('#upload-form');
const fileInput = $('#file-input');
const uploadButton = $('#upload-button');
const statusLine = $('#status');
const progressBar = $('#progress-bar');
const fileList = $('#file-list');
let sessionToken = null;
let refreshTimer = null;
let toastTimer = null;

function showOnly(panel) {
  for (const section of [welcome, loading, sessionPanel, errorPanel]) section.hidden = section !== panel;
}

function showError(title, message) {
  $('#error-title').textContent = title;
  $('#error-message').textContent = message;
  showOnly(errorPanel);
}

function notify(message) {
  const toast = $('#toast');
  toast.textContent = message;
  toast.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove('show'), 2600);
}

function formatSize(size) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function formatDate(milliseconds) {
  return new Date(milliseconds).toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
}

async function readJson(response) {
  let payload = {};
  try { payload = await response.json(); } catch { /* handled below */ }
  if (!response.ok) throw new Error(payload.error || 'Não foi possível concluir a operação.');
  return payload;
}

async function refreshFiles() {
  if (!sessionToken) return false;
  try {
    const response = await fetch(`/api/files/${encodeURIComponent(sessionToken)}`, { cache: 'no-store' });
    const payload = await readJson(response);
    fileList.replaceChildren();
    $('#file-summary').textContent = payload.files.length
      ? `${payload.files.length} arquivo${payload.files.length === 1 ? '' : 's'} disponível${payload.files.length === 1 ? '' : 'is'}`
      : 'Ainda não há arquivos nesta sessão.';
    if (!payload.files.length) {
      const empty = document.createElement('li');
      empty.className = 'empty';
      empty.textContent = 'Os arquivos enviados aparecerão aqui.';
      fileList.append(empty);
      return true;
    }
    for (const file of payload.files) {
      const item = document.createElement('li');
      const info = document.createElement('div');
      const name = document.createElement('span');
      const meta = document.createElement('span');
      const download = document.createElement('a');
      name.className = 'file-name';
      name.textContent = file.name;
      meta.className = 'file-meta';
      meta.textContent = `${formatSize(file.size)} · ${formatDate(file.uploaded)}`;
      name.append(meta);
      download.className = 'download';
      download.href = `/api/download/${encodeURIComponent(sessionToken)}/${encodeURIComponent(file.id)}`;
      download.textContent = 'Baixar';
      info.append(name);
      item.append(info, download);
      fileList.append(item);
    }
    return true;
  } catch (error) {
    if (/inválida|expirada/i.test(error.message)) {
      clearInterval(refreshTimer);
      showError('Sessão expirada', 'Esta transferência expirou após 24 horas. Crie uma nova sessão para continuar.');
    } else {
      $('#file-summary').textContent = 'Não foi possível atualizar a lista.';
    }
    return false;
  }
}

async function openSession(token) {
  if (!/^[A-Za-z0-9_-]{40,48}$/.test(token)) {
    showError('Link inválido', 'O endereço desta sessão não parece válido. Crie uma nova transferência.');
    return;
  }
  sessionToken = token;
  showOnly(loading);
  $('#loading-text').textContent = 'Verificando sua sessão…';
  try {
    const url = new URL(location.href);
    url.searchParams.set('session', token);
    const shareUrl = url.toString();
    const link = $('#share-link');
    link.href = shareUrl;
    link.textContent = shareUrl;
    await QRCode.toCanvas($('#qr'), shareUrl, {
      width: 308,
      margin: 1,
      errorCorrectionLevel: 'M',
      color: { dark: '#111713', light: '#ffffff' },
    });
    showOnly(sessionPanel);
    const valid = await refreshFiles();
    clearInterval(refreshTimer);
    if (valid) refreshTimer = setInterval(refreshFiles, 5000);
  } catch (error) {
    showError('Sessão indisponível', error.message || 'O link expirou ou não pôde ser carregado.');
  }
}

async function createSession() {
  const button = $('#create-session');
  button.disabled = true;
  showOnly(loading);
  $('#loading-text').textContent = 'Criando uma sessão segura…';
  try {
    const payload = await readJson(await fetch('/api/sessions', { method: 'POST' }));
    history.replaceState(null, '', `/?session=${encodeURIComponent(payload.token)}`);
    await openSession(payload.token);
  } catch (error) {
    showOnly(welcome);
    notify(error.message);
  } finally {
    button.disabled = false;
  }
}

function uploadOne(file, completedBytes, totalBytes) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `/api/upload/${encodeURIComponent(sessionToken)}?name=${encodeURIComponent(file.name)}`);
    xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream');
    xhr.upload.onprogress = event => {
      if (!event.lengthComputable) return;
      const percent = Math.min(100, Math.round((completedBytes + event.loaded) / totalBytes * 100));
      progressBar.style.width = `${percent}%`;
    };
    xhr.onload = () => {
      let payload = {};
      try { payload = JSON.parse(xhr.responseText); } catch { /* handled below */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(payload);
      else reject(new Error(payload.error || 'Falha ao enviar o arquivo.'));
    };
    xhr.onerror = () => reject(new Error('Falha de conexão durante o envio.'));
    xhr.onabort = () => reject(new Error('Envio cancelado.'));
    xhr.send(file);
  });
}

$('#create-session').addEventListener('click', createSession);
$('#back-home').addEventListener('click', () => {
  clearInterval(refreshTimer);
  sessionToken = null;
  history.replaceState(null, '', '/');
  showOnly(welcome);
});
$('#copy-link').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText($('#share-link').href);
    notify('Link copiado. Compartilhe apenas com quem deve ter acesso.');
  } catch {
    notify('Não consegui copiar automaticamente. Selecione e copie o link.');
  }
});
$('#refresh-files').addEventListener('click', refreshFiles);
fileInput.addEventListener('change', () => {
  const count = fileInput.files.length;
  $('#file-count').textContent = count ? `${count} arquivo${count === 1 ? '' : 's'} selecionado${count === 1 ? '' : 's'}` : 'Nenhum arquivo escolhido';
});
uploadForm.addEventListener('submit', async event => {
  event.preventDefault();
  const files = [...fileInput.files];
  if (!files.length) return;
  if (files.length > 10) {
    statusLine.textContent = 'Escolha no máximo 10 arquivos por envio.';
    return;
  }
  const tooLarge = files.find(file => file.size > 50 * 1024 * 1024);
  if (tooLarge) {
    statusLine.textContent = `${tooLarge.name} ultrapassa o limite de 50 MB.`;
    return;
  }
  const totalBytes = files.reduce((sum, file) => sum + file.size, 0);
  let completedBytes = 0;
  uploadButton.disabled = true;
  statusLine.textContent = `Enviando 0 de ${files.length} arquivo${files.length === 1 ? '' : 's'}…`;
  progressBar.style.width = '0%';
  try {
    for (const [index, file] of files.entries()) {
      await uploadOne(file, completedBytes, totalBytes || 1);
      completedBytes += file.size;
      statusLine.textContent = `Enviando ${index + 1} de ${files.length} arquivo${files.length === 1 ? '' : 's'}…`;
    }
    statusLine.textContent = 'Envio concluído.';
    progressBar.style.width = '100%';
    uploadForm.reset();
    $('#file-count').textContent = 'Nenhum arquivo escolhido';
    await refreshFiles();
  } catch (error) {
    statusLine.textContent = error.message;
    await refreshFiles();
  } finally {
    uploadButton.disabled = false;
  }
});

const initialToken = new URLSearchParams(location.search).get('session');
if (initialToken) openSession(initialToken);
else showOnly(welcome);
