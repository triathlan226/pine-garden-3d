import './style.css';
import { loadContent, el, paragraphs, CATEGORY_CLASS } from './content.js';

const NAV = [
  ['index.html', '3D 導覽', 'enter'],
  ['about.html', '關於松園別館'],
  ['list.html', '熱點清單（2D）'],
  ['sources.html', '資料來源'],
  ['credits.html', '製作說明'],
];

function header() {
  const here = location.pathname.split('/').pop() || 'index.html';
  const nav = el('nav', { 'aria-label': '網站' }, ...NAV.map(([href, label, cls]) => {
    const a = el('a', { href: './' + href, text: label });
    if (cls) a.className = cls;
    if (href === here) a.setAttribute('aria-current', 'page');
    return a;
  }));
  const h = el('header', { class: 'page-head' }, el('div', { class: 'inner' }, el('a', { class: 'logo', href: './index.html', text: '松園別館 3D 導覽' }), nav));
  document.body.prepend(h);
  document.body.append(el('footer', { class: 'page-foot' },
    el('p', { text: '本站為非官方的教育性 3D 重建導覽，內容依公開資料整理。開放時間、票價等請以松園別館官網公告為準。' })));
}

const badge = (cat) => el('span', { class: 'badge ' + (CATEGORY_CLASS[cat] || 'recon'), text: cat });

function srcLinks(ids, sources) {
  const frag = el('span', { class: 'small' });
  ids.forEach((id, i) => {
    const s = sources[id];
    if (!s) return;
    if (i) frag.append('、');
    frag.append(el('a', { href: s.url, target: '_blank', rel: 'noopener', text: id }));
  });
  return frag;
}

async function renderList(main, c) {
  const reason = new URLSearchParams(location.search).get('reason');
  if (reason === 'webgl') main.append(el('p', { class: 'note', text: '您的瀏覽器或裝置不支援 WebGL 2，已自動切換到 2D 版熱點清單。內容與 3D 版完全相同。' }));
  const filter = el('div', { class: 'filter', role: 'group', 'aria-label': '依類別篩選' });
  const cats = ['全部', '史實', '流傳說法', '推測重建'];
  const grid = el('div', { class: 'cards' });
  const draw = (cat) => {
    grid.replaceChildren(...c.hotspots.filter((h) => cat === '全部' || h.category === cat).map((h) => {
      const body = el('div', { class: 'body' }, ...paragraphs(h.body).map((p) => el('p', { text: p })));
      const src = el('ol', { class: 'small' }, ...h.sourceList.map((s) => el('li', {}, el('a', { href: s.url, target: '_blank', rel: 'noopener', text: s.title }))));
      return el('article', { class: 'card', id: h.id },
        el('p', { class: 'legend-row' }, badge(h.category), el('span', { class: 'small', text: `第 ${h.order} 站` })),
        el('h3', { text: h.title }),
        el('p', { text: h.summary }),
        el('details', {}, el('summary', { text: '閱讀解說' }), body, el('p', { class: 'small', text: '資料來源：' }), src));
    }));
    for (const b of filter.children) b.setAttribute('aria-pressed', String(b.dataset.cat === cat));
  };
  for (const cat of cats) {
    const b = el('button', { type: 'button', text: cat });
    b.dataset.cat = cat;
    b.onclick = () => draw(cat);
    filter.append(b);
  }
  main.append(filter, grid);
  draw('全部');
}

async function renderSources(main, c) {
  main.append(el('p', { text: `最後查證日期：${c.checked}。網站上標為「史實」的解說，至少對應下表一個來源；只有二手資料或沒有出處的內容，標為「流傳說法」或「推測重建」。` }));
  const tb = el('tbody');
  for (const s of c.sourceList) {
    const used = c.hotspots.filter((h) => h.sources.includes(s.id)).map((h) => h.title);
    tb.append(el('tr', {}, el('td', { text: s.id }),
      el('td', {}, el('a', { href: s.url, target: '_blank', rel: 'noopener', text: s.title })),
      el('td', { text: s.level }),
      el('td', { class: 'small', text: used.join('、') || '—' })));
  }
  main.append(el('table', { class: 'src-table' },
    el('thead', {}, el('tr', {}, el('th', { text: 'ID' }), el('th', { text: '來源' }), el('th', { text: '等級' }), el('th', { text: '引用於' }))), tb));
}

async function main() {
  header();
  const main = document.querySelector('main');
  const kind = document.body.dataset.page;
  if (kind === 'list' || kind === 'sources' || kind === 'about') {
    try {
      const c = await loadContent();
      if (kind === 'list') await renderList(main.querySelector('[data-slot]') || main, c);
      if (kind === 'sources') await renderSources(main.querySelector('[data-slot]') || main, c);
      if (kind === 'about') {
        for (const node of document.querySelectorAll('[data-src]')) node.append(' ', srcLinks(node.dataset.src.split(','), c.sources));
      }
    } catch (e) {
      main.append(el('p', { class: 'note', text: '內容載入失敗：' + e.message }));
    }
  }
  for (const node of document.querySelectorAll('[data-badge]')) node.prepend(badge(node.dataset.badge), ' ');
}
main();
