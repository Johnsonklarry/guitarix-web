window.ForgeLayout = {
  MIN_REFRESH_INTERVAL: 1000,
  MAX_REFRESH_INTERVAL: 300000,

  renderers: {
    'storage-usage': function renderStorageUsage(bodyEl, options, tile) {
      const source = (tile && (tile.usage_payload || tile.data))
        || (options && (options.usage_payload || options.data));
      if (!source || source.status === 'unavailable') {
        window.ForgeLayout.renderUnavailable(bodyEl, 'Storage unavailable');
        return null;
      }
      const usage = source.usage || {};
      const container = document.createElement('div');
      container.className = 'forge-storage-usage';
      const usedEl = document.createElement('div');
      usedEl.className = 'forge-storage-used';
      usedEl.textContent = `Used: ${usage.used != null ? usage.used : 0}`;
      const totalEl = document.createElement('div');
      totalEl.className = 'forge-storage-total';
      totalEl.textContent = `Total: ${usage.total != null ? usage.total : 0}`;
      container.appendChild(usedEl);
      container.appendChild(totalEl);
      bodyEl.appendChild(container);
      return null;
    },

    'calibre-recent-books': function renderCalibreRecent(bodyEl, options, tile) {
      const source = (tile && (tile.books_payload || tile.data))
        || (options && (options.books_payload || options.data));
      if (!source || source.status === 'unavailable') {
        window.ForgeLayout.renderUnavailable(bodyEl, 'Unavailable');
        return null;
      }
      const list = document.createElement('ul');
      list.className = 'forge-calibre-books';
      (source.books || []).forEach(book => {
        const item = document.createElement('li');
        item.textContent = book.title || 'Untitled';
        list.appendChild(item);
      });
      bodyEl.appendChild(list);
      return null;
    },

    'nextcloud-summary': function renderNextcloudSummary(bodyEl, options, tile) {
      const opts = options || {};
      const source = (tile && (tile.nextcloud_payload || tile.data))
        || (opts.nextcloud_payload || opts.data);

      const renderSource = (data) => {
        if (!data || data.authorized === false || data.status === 'unconfigured') {
          window.ForgeLayout.renderUnavailable(bodyEl, 'Unconfigured');
          return;
        }
        if (data.status === 'unavailable') {
          window.ForgeLayout.renderUnavailable(bodyEl, 'Offline');
          return;
        }
        const wrapper = document.createElement('div');
        wrapper.className = 'forge-nextcloud-summary';

        const events = document.createElement('ul');
        events.className = 'forge-nextcloud-events';
        (data.events || []).forEach(event => {
          const item = document.createElement('li');
          item.textContent = event.title || 'Untitled event';
          events.appendChild(item);
        });
        wrapper.appendChild(events);

        const files = document.createElement('ul');
        files.className = 'forge-nextcloud-files';
        (data.files || []).forEach(file => {
          const item = document.createElement('li');
          item.textContent = file.name || 'Untitled file';
          files.appendChild(item);
        });
        wrapper.appendChild(files);

        bodyEl.appendChild(wrapper);
      };

      if (typeof opts.summary_provider === 'function') {
        let provided;
        try {
          provided = opts.summary_provider();
        } catch (err) {
          window.ForgeLayout.renderUnavailable(bodyEl, 'Offline');
          return null;
        }
        if (provided && typeof provided.then === 'function') {
          provided.then(renderSource, () => {
            window.ForgeLayout.renderUnavailable(bodyEl, 'Offline');
          });
        } else {
          renderSource(provided);
        }
        return null;
      }

      renderSource(source);
      return null;
    },

    'status-health': function renderStatusHealth(bodyEl, options, tile) {
      const source = (tile && (tile.health || tile.data))
        || (options && (options.health || options.data));
      const wrapper = document.createElement('div');
      wrapper.className = 'forge-status-health';

      if (!source) {
        wrapper.classList.add('forge-status-health-unknown');
        const unknown = document.createElement('div');
        unknown.className = 'forge-status-health-state';
        unknown.textContent = 'unknown';
        wrapper.appendChild(unknown);
        bodyEl.appendChild(wrapper);
        return null;
      }

      const entries = Array.isArray(source) ? source : (source.entries || []);
      const limit = options && Number.isInteger(options.limit) ? options.limit : entries.length;
      const showOk = !!(options && options.show_ok);
      const list = document.createElement('ul');
      list.className = 'forge-status-health-list';

      entries
        .filter(entry => showOk || (entry.status && entry.status !== 'ok'))
        .slice(0, limit)
        .forEach(entry => {
          const item = document.createElement('li');
          item.className = 'forge-status-health-item';
          item.dataset.status = entry.status || 'unknown';
          const name = document.createElement('span');
          name.className = 'forge-status-health-name';
          name.textContent = entry.name || 'unnamed';
          item.appendChild(name);
          list.appendChild(item);
        });

      if (list.childNodes.length) {
        wrapper.appendChild(list);
      } else {
        const empty = document.createElement('div');
        empty.className = 'forge-status-health-empty';
        empty.textContent = 'No status data';
        wrapper.appendChild(empty);
      }
      bodyEl.appendChild(wrapper);
      return null;
    },

    'stack-drift': function renderStackDrift(bodyEl, options, tile) {
      const source = (tile && (tile.drift || tile.data))
        || (options && (options.drift || options.data));
      const wrapper = document.createElement('div');
      wrapper.className = 'forge-stack-drift';

      if (!source) {
        wrapper.classList.add('forge-stack-drift-unknown');
        const unknown = document.createElement('div');
        unknown.className = 'forge-stack-drift-state';
        unknown.textContent = 'unknown';
        wrapper.appendChild(unknown);
        bodyEl.appendChild(wrapper);
        return null;
      }

      const stacks = Array.isArray(source) ? source : (source.stacks || []);
      const limit = options && Number.isInteger(options.limit) ? options.limit : stacks.length;
      const showSynced = !!(options && options.show_synced);
      const list = document.createElement('ul');
      list.className = 'forge-stack-drift-list';

      stacks
        .filter(stack => showSynced || (stack.status && stack.status !== 'synced'))
        .slice(0, limit)
        .forEach(stack => {
          const item = document.createElement('li');
          item.className = 'forge-stack-drift-item';
          item.dataset.status = stack.status || 'unknown';
          const name = document.createElement('span');
          name.className = 'forge-stack-drift-name';
          name.textContent = stack.name || 'unnamed';
          const status = document.createElement('span');
          status.className = 'forge-stack-drift-status';
          status.textContent = stack.status || 'unknown';
          item.appendChild(name);
          item.appendChild(status);
          list.appendChild(item);
        });

      if (list.childNodes.length) {
        wrapper.appendChild(list);
      } else {
        const empty = document.createElement('div');
        empty.className = 'forge-stack-drift-empty';
        empty.textContent = 'No stack drift';
        wrapper.appendChild(empty);
      }
      bodyEl.appendChild(wrapper);
      return null;
    },

    'node-red-flows': function renderNodeRedFlows(bodyEl, options, tile) {
      const source = (tile && (tile.flows_payload || tile.data))
        || (options && (options.flows_payload || options.data));
      if (!source || source.status === 'unavailable') {
        window.ForgeLayout.renderUnavailable(bodyEl, 'Unavailable');
        return null;
      }
      const list = document.createElement('ul');
      list.className = 'forge-node-red-flows';
      (source.flows || []).forEach(flow => {
        const item = document.createElement('li');
        item.className = 'forge-node-red-flow';
        item.dataset.status = flow.status || 'unknown';
        item.textContent = flow.name || 'unnamed';
        list.appendChild(item);
      });
      bodyEl.appendChild(list);
      return null;
    },

    'node-red-log': function renderNodeRedLog(bodyEl, options, tile) {
      const source = (tile && (tile.log_payload || tile.data))
        || (options && (options.log_payload || options.data));
      if (!source || source.status === 'unavailable') {
        window.ForgeLayout.renderUnavailable(bodyEl, 'Unavailable');
        return null;
      }
      const list = document.createElement('ul');
      list.className = 'forge-node-red-log';
      (source.events || []).forEach(event => {
        const item = document.createElement('li');
        item.className = 'forge-node-red-log-entry';
        const timestamp = document.createElement('span');
        timestamp.className = 'forge-node-red-timestamp';
        timestamp.textContent = event.timestamp || '';
        const message = document.createElement('span');
        message.className = 'forge-node-red-message';
        message.textContent = event.message || '';
        item.appendChild(timestamp);
        item.appendChild(message);
        list.appendChild(item);
      });
      bodyEl.appendChild(list);
      return null;
    }
  },

  render(container, preset, renderers, opts) {
    const grid = document.createElement('div');
    grid.className = 'forge-grid';
    grid.style.gridTemplateColumns = `repeat(${preset.columns}, 1fr)`;
    grid.style.gridAutoRows = `${preset.row_height}px`;

    const cleanups = [];
    const timers = [];

    // Positional (y, x) order, so the stacked phone layout reads top to bottom, left to right.
    const ordered = [...(preset.tiles || [])].sort((a, b) => (a.y - b.y) || (a.x - b.x));
    const registryTiles = (opts && opts.registry && opts.registry.tiles) || [];
    ordered.forEach(tile => {
      const tileEl = document.createElement('section');
      tileEl.className = 'forge-tile';
      tileEl.dataset.type = tile.type;
      tileEl.style.gridColumn = `${tile.x + 1} / span ${tile.w}`;
      tileEl.style.gridRow = `${tile.y + 1} / span ${tile.h}`;

      const titleBar = document.createElement('header');
      titleBar.className = 'forge-tile-title';
      const tileDef = registryTiles.find(t => t.type === tile.type);
      titleBar.textContent = tileDef ? tileDef.title : 'Unknown Tile';
      tileEl.appendChild(titleBar);

      const bodyEl = document.createElement('div');
      bodyEl.className = 'forge-tile-body';
      tileEl.appendChild(bodyEl);

      const renderTile = () => {
        let activeCleanup = null;
        try {
          const renderer = renderers[tile.type]
            || (window.ForgeLayout.renderers || {})[tile.type];
          if (renderer) {
            activeCleanup = renderer(bodyEl, tile.options, tile);
            if (activeCleanup) cleanups.push(activeCleanup);
          } else {
            this.renderUnavailable(bodyEl, 'Unknown tile type');
          }
        } catch (err) {
          this.renderUnavailable(bodyEl, 'Tile unavailable');
        }
        return activeCleanup;
      };

      renderTile();

      const payload = (tile && tile.data) || {};
      const refreshInterval = typeof payload.refresh_seconds === 'number'
        ? payload.refresh_seconds * 1000
        : (tile.options && tile.options.refresh_interval);
      const payloadFloor = typeof payload.min_refresh_seconds === 'number'
        ? payload.min_refresh_seconds * 1000
        : 0;
      if (typeof refreshInterval === 'number' && refreshInterval > 0) {
        const boundedInterval = Math.max(
          this.MIN_REFRESH_INTERVAL,
          payloadFloor,
          Math.min(refreshInterval, this.MAX_REFRESH_INTERVAL)
        );
        const timerId = setInterval(() => {
          renderTile();
        }, boundedInterval);
        timers.push(timerId);
      }

      grid.appendChild(tileEl);
    });

    container.appendChild(grid);

    const handleResize = () => {
      if (window.innerWidth < 720) {
        grid.classList.add('forge-grid-stacked');
      } else {
        grid.classList.remove('forge-grid-stacked');
      }
    };

    window.addEventListener('resize', handleResize);
    handleResize();

    return {
      destroy() {
        timers.forEach(timerId => clearInterval(timerId));
        cleanups.forEach(cleanup => cleanup());
        window.removeEventListener('resize', handleResize);
        container.innerHTML = '';
      }
    };
  },

  renderUnavailable(container, message) {
    container.innerHTML = '';
    const unavailableEl = document.createElement('div');
    unavailableEl.className = 'forge-tile-unavailable forge-tile-placeholder';
    unavailableEl.textContent = message || 'Tile unavailable';
    container.appendChild(unavailableEl);
  },

  gridStyle(preset) {
    return {
      display: 'grid',
      gridTemplateColumns: `repeat(${preset.columns}, 1fr)`,
      gridAutoRows: `${preset.row_height}px`
    };
  }
};
