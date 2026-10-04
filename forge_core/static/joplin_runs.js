(function () {
  'use strict';

  // Read-only view of delegation-run notes. The notes arrive already fetched
  // by the server in the tile data; this module never contacts Joplin itself.

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function clear(bodyEl) {
    while (bodyEl.firstChild) bodyEl.removeChild(bodyEl.firstChild);
  }

  function renderUnavailable(bodyEl, message) {
    clear(bodyEl);
    const wrapper = el('div', 'forge-joplin-runs forge-joplin-runs-unavailable');
    wrapper.appendChild(el('div', 'forge-joplin-runs-state', message || 'Joplin runs unavailable'));
    bodyEl.appendChild(wrapper);
    return null;
  }

  function renderJoplinRuns(bodyEl, options, tile) {
    const source = (tile && (tile.runs || tile.data))
      || (options && (options.runs || options.data));
    if (!source || source.available === false) {
      return renderUnavailable(bodyEl, source && source.message);
    }
    const notes = Array.isArray(source)
      ? source
      : (Array.isArray(source.notes) ? source.notes : null);
    if (!notes) {
      return renderUnavailable(bodyEl);
    }

    clear(bodyEl);
    const limit = options && Number.isInteger(options.limit) ? options.limit : notes.length;
    const wrapper = el('div', 'forge-joplin-runs');
    const list = el('ul', 'forge-joplin-runs-list');

    notes.slice(0, limit).forEach(note => {
      const item = el('li', 'forge-joplin-runs-item');
      item.appendChild(el('div', 'forge-joplin-runs-title', (note && note.title) || 'Untitled run'));
      if (note && note.updated_time) {
        item.appendChild(el('div', 'forge-joplin-runs-updated', note.updated_time));
      }
      if (note && note.body) {
        item.appendChild(el('div', 'forge-joplin-runs-body', note.body));
      }
      list.appendChild(item);
    });

    if (list.childNodes.length) {
      wrapper.appendChild(list);
    } else {
      wrapper.appendChild(el('div', 'forge-joplin-runs-empty', 'No delegation runs'));
    }
    bodyEl.appendChild(wrapper);
    return null;
  }

  window.ForgeJoplinRuns = {
    render: renderJoplinRuns,
    renderUnavailable: renderUnavailable
  };
  if (window.ForgeLayout) {
    window.ForgeLayout.renderers = window.ForgeLayout.renderers || {};
    window.ForgeLayout.renderers['joplin-runs'] = renderJoplinRuns;
  }
})();
