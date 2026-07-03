/**
 * Бензин рядом — Telegram + VK Mini App
 * Modern, single-page app with full bot functionality
 */
(function () {
  'use strict';

  // ============= PLATFORM DETECTION =============
  const platform = {
    tg: !!(window.Telegram && window.Telegram.WebApp),
    vk: false, // determined async via VK Bridge
  };

  const tg = platform.tg ? window.Telegram.WebApp : null;

  if (tg) {
    tg.ready();
    tg.expand();
    if (tg.colorScheme === 'light') {
      document.body.classList.add('tg-light');
    }
  }

  // VK Bridge detection
  const vkBridgePromise = (async () => {
    if (window.vkBridge) {
      try {
        await window.vkBridge.send('VKWebAppInit', {});
        platform.vk = true;
        return true;
      } catch (e) {
        return false;
      }
    }
    return false;
  })();

  // ============= API =============
  const API = (() => {
    const params = new URLSearchParams(window.location.search);
    const apiBase = params.get('api') || '';
    return apiBase || window.location.origin;
  })();

  async function api(path, options = {}) {
    const url = `${API}${path}`;
    const headers = { 'Content-Type': 'application/json' };
    if (tg?.initData) headers['X-Telegram-Init-Data'] = tg.initData;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 15000); // 15s timeout
    try {
      const res = await fetch(url, {
        ...options,
        signal: controller.signal,
        headers: { ...headers, ...(options.headers || {}) },
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      return data;
    } catch (e) {
      if (e.name === 'AbortError') throw new Error('Таймаут запроса (15с)');
      throw e;
    } finally {
      clearTimeout(timeout);
    }
  }

  // ============= STATE =============
  const state = {
    screen: 'home',
    tab: 'home',
    city: '',
    cityRegion: '',
    fuel: '',
    searchQuery: '',
    stations: [],
    userLocation: null, // { lat, lon }
    selectedStation: null,
    reportSheet: {
      stationId: null,
      stationName: '',
      fuel: '92',
      available: true,
      price: null,
      queue: null,
    },
    reviewSheet: {
      stationId: null,
      stationName: '',
      fuel: '92',
      rating: 0,
      comment: '',
    },
    cities: [], // popular cities
  };

  // ============= DOM =============
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const dom = {
    app: $('#app'),
    main: $('#main'),
    stationsList: $('#stations-list'),
    emptyState: $('#empty-state'),
    resultsTitle: $('#results-title'),
    resultsCount: $('#results-count'),
    citySelector: $('#city-selector'),
    currentCity: $('#current-city'),
    searchInput: $('#search-input'),
    searchClear: $('#search-clear'),
    geoBtn: $('#btn-geo'),
    emergencyBtn: $('#btn-emergency'),
    profileAvatar: $('#profile-avatar'),
    profileBigAvatar: $('#profile-big-avatar'),
    profileName: $('#profile-name'),
    profileId: $('#profile-id'),
    statReports: $('#stat-reports'),
    statReviews: $('#stat-reviews'),
    statBadges: $('#stat-badges'),
    badgesGrid: $('#badges-grid'),
    subsList: $('#subs-list'),
    citySearch: $('#city-search'),
    citiesList: $('#cities-list'),
    reportSheet: $('#report-sheet'),
    reportSheetStation: $('#report-sheet-station'),
    reportPrice: $('#report-price'),
    reportQueue: $('#report-queue'),
    reviewSheet: $('#review-sheet'),
    reviewSheetStation: $('#review-sheet-station'),
    reviewComment: $('#review-comment'),
    starsRow: $('#stars-row'),
    ratingHint: $('#rating-hint'),
    toast: $('#toast'),
    loadingOverlay: $('#loading-overlay'),
  };

  // ============= UTILS =============
  function showToast(message, type = '') {
    dom.toast.textContent = message;
    dom.toast.className = `toast ${type}`;
    dom.toast.hidden = false;
    clearTimeout(dom.toast._timer);
    dom.toast._timer = setTimeout(() => { dom.toast.hidden = true; }, 2400);
  }

  function showLoading() { dom.loadingOverlay.hidden = false; }
  function hideLoading() { dom.loadingOverlay.hidden = true; }

  // Inline skeleton (shown in stations list, not full-screen)
  function showSkeletons() {
    dom.stationsList.innerHTML = '';
    for (let i = 0; i < 3; i++) {
      const sk = document.createElement('div');
      sk.className = 'station-card skeleton';
      sk.innerHTML = `
        <div class="skeleton-line w70"></div>
        <div class="skeleton-line w40"></div>
        <div class="skeleton-line w90"></div>
      `;
      dom.stationsList.appendChild(sk);
    }
    dom.emptyState.hidden = true;
  }

  function formatTimeAgo(iso) {
    if (!iso) return '';
    const t = typeof iso === 'string' ? new Date(iso) : iso;
    const diff = Date.now() - t.getTime();
    if (diff < 0) return 'только что';
    const m = Math.floor(diff / 60000);
    if (m < 1) return 'только что';
    if (m < 60) return `${m} мин назад`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h} ч назад`;
    const d = Math.floor(h / 24);
    if (d < 7) return `${d} дн назад`;
    return t.toLocaleDateString('ru-RU');
  }

  function fuelLabel(f) {
    if (f === 'diesel') return 'Дизель';
    if (f === 'lpg') return 'Газ';
    if (f === '92' || f === '95' || f === '98' || f === '100') return `АИ-${f}`;
    return f || '';
  }

  function getTgId() {
    if (tg?.initDataUnsafe?.user?.id) return tg.initDataUnsafe.user.id;
    // VK uses peer_id
    if (platform.vk && window.vkBridge) {
      // Sync return: VK id is fetched async separately
      return state.vkUserId;
    }
    return null;
  }

  // ============= HAPTIC =============
  function haptic(style) {
    if (tg?.HapticFeedback) {
      try { tg.HapticFeedback.impactOccurred(style || 'light'); } catch (e) {}
    }
  }

  function hapticNotify(type) {
    if (tg?.HapticFeedback) {
      try { tg.HapticFeedback.notificationOccurred(type || 'success'); } catch (e) {}
    }
  }

  // ============= NAVIGATION =============
  function setTab(tab) {
    state.tab = tab;
    $$('.nav-item').forEach(b => b.classList.toggle('active', b.dataset.tab === tab));
    if (tab === 'home') showScreen('home');
    else if (tab === 'map') {
      showScreen('map');
      loadMap();
    }
    else if (tab === 'report') openReportFlow();
    else if (tab === 'profile') {
      showScreen('profile');
      loadProfile();
    }
  }

  function showScreen(name) {
    $$('.screen').forEach(s => s.classList.toggle('active', s.dataset.screen === name));
    state.screen = name;
    window.scrollTo({ top: 0, behavior: 'smooth' });
  }

  // ============= CITY =============
  function setCity(city, region) {
    state.city = city;
    state.cityRegion = region || '';
    dom.currentCity.textContent = city;
    try {
      localStorage.setItem('benzin_city', city);
      if (region) localStorage.setItem('benzin_region', region);
    } catch (e) {}
    loadStations();
  }

  async function showCityPicker() {
    showScreen('cities');
    dom.citySearch.value = '';
    await renderCities();
  }

  async function renderCities(query = '') {
    if (state.cities.length === 0) {
      try {
        const data = await api('/api/search?q=');
        state.cities = (data.stations || []).slice(0, 20);
      } catch (e) {}
    }
    // For now show top cities - we don't have a /cities endpoint
    // Will use Moscow, SPb, etc as defaults if no data
    const popular = ['Москва', 'Санкт-Петербург', 'Новосибирск', 'Екатеринбург',
      'Казань', 'Нижний Новгород', 'Челябинск', 'Самара', 'Омск', 'Ростов-на-Дону',
      'Уфа', 'Красноярск', 'Воронеж', 'Пермь', 'Волгоград', 'Краснодар',
      'Саратов', 'Тюмень', 'Тольятти', 'Ижевск', 'Барнаул', 'Иркутск',
      'Ульяновск', 'Хабаровск', 'Владивосток', 'Ярославль', 'Махачкала',
      'Томск', 'Оренбург', 'Кемерово', 'Новокузнецк', 'Рязань', 'Астрахань',
      'Пенза', 'Липецк', 'Тула', 'Киров', 'Чебоксары', 'Калининград',
      'Брянск', 'Курск', 'Иваново', 'Магнитогорск', 'Улан-Удэ', 'Тверь',
      'Ставрополь', 'Белгород', 'Архангельск', 'Владимир', 'Сочи', 'Калуга',
      'Сургут', 'Смоленск', 'Вологда', 'Чита', 'Каменск-Уральский'];
    const q = query.trim().toLowerCase();
    const filtered = q ? popular.filter(c => c.toLowerCase().includes(q)) : popular;

    dom.citiesList.innerHTML = '';
    if (filtered.length === 0) {
      dom.citiesList.innerHTML = '<div class="empty-mini">Ничего не найдено</div>';
      return;
    }
    filtered.forEach(city => {
      const item = document.createElement('div');
      item.className = 'city-item';
      item.innerHTML = `
        <div class="city-item-icon">📍</div>
        <div class="city-item-name">${city}</div>
        <div class="city-item-count">›</div>
      `;
      item.addEventListener('click', () => {
        haptic('light');
        setCity(city);
        showScreen('home');
      });
      dom.citiesList.appendChild(item);
    });
  }

  // ============= STATIONS =============
  async function loadStations() {
    if (!state.city) {
      dom.stationsList.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">📍</div>
          <div class="empty-title">Выбери город</div>
          <div class="empty-subtitle">Нажми на панель города выше</div>
        </div>
      `;
      dom.emptyState.hidden = true;
      dom.resultsCount.textContent = '0';
      return;
    }
    // Show inline skeletons (not full-screen overlay)
    showSkeletons();
    try {
      const params = new URLSearchParams();
      params.set('city', state.city);
      if (state.region) params.set('region', state.region);
      if (state.fuel) params.set('fuel', state.fuel);
      params.set('limit', '50');
      const data = await api('/api/stations/by-city?' + params);
      state.stations = data.stations || [];
      renderStations();
    } catch (e) {
      showToast('Ошибка загрузки: ' + e.message, 'error');
      state.stations = [];
      dom.stationsList.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">⚠️</div>
          <div class="empty-title">Не удалось загрузить</div>
          <div class="empty-subtitle">${escape(e.message)}</div>
        </div>
      `;
      dom.emptyState.hidden = true;
      dom.resultsCount.textContent = '0';
    }
  }

  function renderStations() {
    dom.stationsList.innerHTML = '';
    dom.emptyState.hidden = state.stations.length > 0;

    state.stations.forEach((s, i) => {
      const card = createStationCard(s);
      card.style.animationDelay = `${Math.min(i * 0.03, 0.2)}s`;
      dom.stationsList.appendChild(card);
    });
    dom.resultsCount.textContent = state.stations.length;
  }

  function createStationCard(s) {
    const card = document.createElement('div');
    card.className = 'station-card';

    const operator = s.operator || s.name || 'АЗС';
    const address = s.address || '';
    const city = s.city || '';
    const verified = s.is_verified ? '<span class="station-verified">✓</span>' : '';
    const rating = s.avg_rating || s.rating;

    // Format prices
    const statuses = s.statuses || [];
    const prices = statuses
      .filter(st => st.price != null || st.available !== null)
      .slice(0, 4);
    const pricesHtml = prices.map(st => {
      const has = st.available === true;
      const no = st.available === false;
      const empty = st.available === null;
      const price = st.price != null ? `${st.price.toFixed(2)}₽` : '';
      let cls = 'price-chip';
      if (has && price) cls += ' has';
      else if (no) cls += ' no';
      else cls += ' empty';
      const statusIcon = has ? '✓' : no ? '✗' : '?';
      return `<div class="${cls}">${fuelLabel(st.fuel_type)} ${price} ${statusIcon}</div>`;
    }).join('');

    // Updated
    const lastUpdate = statuses[0]?.created_at;
    const updated = lastUpdate ? formatTimeAgo(lastUpdate) : '';

    card.innerHTML = `
      <div class="station-card-row">
        <div class="station-name">${escape(operator)} ${verified}</div>
        ${rating ? `<div class="station-rating">★ ${rating.toFixed(1)}</div>` : ''}
      </div>
      ${address || city ? `
        <div class="station-address">
          <span>${escape(address || city)}</span>
        </div>
      ` : ''}
      ${prices.length > 0 ? `<div class="station-prices">${pricesHtml}</div>` : ''}
      <div class="station-footer">
        <span class="station-updated">${updated ? '🕐 ' + updated : 'Нет данных'}</span>
        <div class="station-actions-mini">
          <button data-action="report" title="Сообщить">📝</button>
        </div>
      </div>
    `;

    card.addEventListener('click', (e) => {
      if (e.target.closest('[data-action="report"]')) {
        e.stopPropagation();
        openReportSheet(s.id, operator);
        return;
      }
      haptic('light');
      openStationDetail(s);
    });

    return card;
  }

  function escape(s) {
    if (!s) return '';
    return String(s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  // ============= STATION DETAIL =============
  async function openStationDetail(s) {
    if (!s || !s.id) {
      showToast('Ошибка: нет данных об АЗС', 'error');
      return;
    }
    state.selectedStation = s;
    showScreen('station');
    // Render skeleton immediately
    const detailEl = $('#station-detail');
    if (detailEl) {
      detailEl.innerHTML = '<div class="map-empty">⏳ Загрузка...</div>';
    }
    try {
      // Load full station data
      const detail = await api(`/api/stations/${s.id}`).catch(e => {
        logger.error('station detail failed:', e);
        return { station: s, statuses: s.statuses || [] };
      });
      // Prices is optional, don't fail if it errors
      const pricesData = await api(`/api/stations/${s.id}/prices`).catch(() => null);
      renderStationDetail(detail, pricesData);
    } catch (e) {
      logger.error('openStationDetail error:', e);
      showToast('Не удалось загрузить: ' + e.message, 'error');
      // Still try to render with what we have
      renderStationDetail({ station: s, statuses: s.statuses || [] }, null);
    }
  }

  function renderStationDetail(detail, pricesData) {
    const s = detail.station || state.selectedStation;
    if (!s) return;
    const statuses = detail.statuses || [];
    const operator = s.operator || s.name || 'АЗС';
    const verified = s.is_verified ? ' ✓' : '';
    const lat = s.lat;
    const lon = s.lon;

    // Fuel rows
    const fuelRows = statuses.length > 0 ? statuses.map(st => {
      const has = st.available === true;
      const no = st.available === false;
      const empty = st.available === null;
      const price = st.price != null ? `${st.price.toFixed(2)} ₽` : '—';
      let rowCls = 'fuel-row';
      if (has) rowCls += ' has-fuel';
      else if (no) rowCls += ' no-fuel';
      else rowCls += ' empty-fuel';
      const statusText = has ? 'В наличии' : no ? 'Нет в наличии' : 'Уточняйте';
      return `
        <div class="${rowCls}">
          <div class="fuel-name">${fuelLabel(st.fuel_type)}</div>
          <div class="fuel-status">
            <span>${statusText}</span>
            <span class="fuel-price">${price}</span>
          </div>
        </div>
      `;
    }).join('') : '<div class="empty-mini">Нет данных о ценах</div>';

    // Last update
    const lastUpdate = statuses[0]?.created_at;
    const updated = lastUpdate ? formatTimeAgo(lastUpdate) : '—';

    // Sources summary from prices API
    let sourcesHtml = '';
    if (pricesData && pricesData.total_sources) {
      const srcs = Object.entries(pricesData.sources_summary || {})
        .map(([src, count]) => `<span class="price-chip">${src}: ${count}</span>`)
        .join('');
      if (srcs) sourcesHtml = `<div class="station-prices">${srcs}</div>`;
    }

    $('#station-detail').innerHTML = `
      <div class="detail-back" data-action="back">‹ Назад</div>

      <div class="detail-card">
        <div class="detail-name">${escape(operator)}${verified}</div>
        ${s.operator && s.name && s.operator !== s.name ?
          `<div class="detail-operator">${escape(s.name)}</div>` : ''}
        ${s.address ? `
          <div class="detail-address">
            <span>📍</span>
            <span>${escape(s.address)}</span>
          </div>
        ` : ''}
        <div class="detail-meta">
          <div class="meta-item">
            <div class="meta-label">Город</div>
            <div class="meta-value">${escape(s.city || '—')}</div>
          </div>
          <div class="meta-item">
            <div class="meta-label">Обновлено</div>
            <div class="meta-value">${updated}</div>
          </div>
        </div>
      </div>

      <div class="section-header">
        <h2 class="section-title">Цены и наличие</h2>
      </div>
      <div class="fuel-prices-list">${fuelRows}</div>

      ${sourcesHtml ? `
        <div class="section-header" style="margin-top:16px;">
          <h2 class="section-title">Источники</h2>
        </div>
        ${sourcesHtml}
      ` : ''}

      <div class="detail-actions">
        <button class="btn btn-primary" data-action="report">📝 Сообщить</button>
        <button class="btn btn-secondary" data-action="review">⭐ Оценить</button>
      </div>

      <div class="detail-actions">
        <button class="btn btn-secondary" data-action="route">🗺️ Маршрут</button>
        <button class="btn btn-secondary" data-action="subscribe">🔔 Подписаться</button>
      </div>

      <div class="section-header" style="margin-top:20px;">
        <h2 class="section-title">Отзывы</h2>
        <span class="section-count" id="reviews-count">0</span>
      </div>
      <div class="reviews-list" id="reviews-list">
        <div class="empty-mini">Пока нет отзывов — будь первым!</div>
      </div>
    `;

    // Bind back button (scoped to station-detail)
    const detailEl2 = $('#station-detail');
    const backBtn = detailEl2.querySelector('[data-action="back"]');
    if (backBtn) backBtn.addEventListener('click', () => showScreen('home'));
    const reportBtn = detailEl2.querySelector('[data-action="report"]');
    if (reportBtn) reportBtn.addEventListener('click', () => openReportSheet(s.id, operator));
    const reviewBtn = detailEl2.querySelector('[data-action="review"]');
    if (reviewBtn) reviewBtn.addEventListener('click', () => openReviewSheet(s.id, operator));
    const routeBtn = detailEl2.querySelector('[data-action="route"]');
    if (routeBtn) routeBtn.addEventListener('click', () => openMap(lat, lon, operator));
    const subBtn = detailEl2.querySelector('[data-action="subscribe"]');
    if (subBtn) subBtn.addEventListener('click', () => subscribeStation(s.id));

    // Load reviews
    loadReviews(s.id);
  }

  async function loadReviews(stationId) {
    // For now, we don't have a public /api/reviews endpoint
    // Reviews are loaded via TG bot. Show placeholder.
    try {
      // Future: GET /api/stations/{id}/reviews
    } catch (e) {}
  }

  // ============= EMERGENCY =============
  async function doEmergencySearch() {
    if (!state.city) {
      showToast('Сначала выбери город', 'warning');
      return;
    }
    showLoading();
    try {
      const data = await api(`/api/stations/emergency?city=${encodeURIComponent(state.city)}&fuel=${state.fuel || '92'}`);
      if (!data.stations || data.stations.length === 0) {
        showToast('К сожалению, в этом городе нет АЗС с подтверждённым наличием', 'warning');
        return;
      }
      state.stations = data.stations;
      dom.resultsTitle.textContent = '🚨 Экстренный поиск';
      renderStations();
      hapticNotify('success');
      showToast(`Найдено ${data.stations.length} АЗС с топливом`, 'success');
    } catch (e) {
      showToast('Ошибка: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  // ============= GEO =============
  async function getUserLocation() {
    return new Promise((resolve) => {
      if (!navigator.geolocation) { resolve(null); return; }
      navigator.geolocation.getCurrentPosition(
        pos => resolve({ lat: pos.coords.latitude, lon: pos.coords.longitude }),
        err => {
          showToast('Не удалось определить местоположение', 'warning');
          resolve(null);
        },
        { timeout: 10000, maximumAge: 60000 }
      );
    });
  }

  async function useGeo() {
    haptic('light');
    const loc = await getUserLocation();
    if (!loc) return;
    state.userLocation = loc;
    // Reverse geocode to get city
    showLoading();
    try {
      const data = await api(`/api/reverse-geocode?lat=${loc.lat}&lon=${loc.lon}`);
      if (data.city) {
        setCity(data.city, data.region);
        showToast(`📍 ${data.city}`, 'success');
      } else {
        showToast('Не удалось определить город', 'warning');
      }
    } catch (e) {
      showToast('Ошибка: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  // ============= MAP =============
  function openMap(lat, lon, name) {
    if (!lat || !lon) {
      showToast('Координаты не указаны', 'warning');
      return;
    }
    // Open in external map app
    const yandex = `yandexmaps://maps.yandex.ru/?pt=${lon},${lat}&z=15`;
    const gmaps = `https://www.google.com/maps/search/?api=1&query=${lat},${lon}`;
    if (tg?.openLink) {
      tg.openLink(gmaps);
    } else {
      window.open(gmaps, '_blank');
    }
  }

  // ============= REPORT FLOW =============
  function openReportFlow() {
    // If no city selected, ask to select first
    if (!state.city) {
      showToast('Сначала выбери город', 'warning');
      showCityPicker();
      return;
    }
    // If we already have stations loaded, show picker
    showStationPicker();
  }

  function showStationPicker() {
    showScreen('pick-station');
    renderStationPicker();
    // Focus search
    setTimeout(() => {
      const inp = document.getElementById('station-picker-search');
      if (inp) {
        inp.value = '';
        inp.addEventListener('input', onStationPickerSearch, { once: false });
      }
    }, 100);
  }

  function renderStationPicker(query = '') {
    const list = document.getElementById('station-picker-list');
    if (!list) return;

    const ql = query.trim();

    // If query is empty — show local stations from current city
    if (!ql) {
      let stations = state.stations || [];
      if (stations.length === 0) {
        // Load stations first
        showLoading();
        const params = new URLSearchParams();
        params.set('city', state.city);
        if (state.fuel) params.set('fuel', state.fuel);
        params.set('limit', '100');
        api('/api/stations/by-city?' + params).then(data => {
          state.stations = data.stations || [];
          renderStationPicker('');
          hideLoading();
        }).catch(e => {
          hideLoading();
          showToast('Ошибка: ' + e.message, 'error');
          list.innerHTML = '<div class="empty-mini">Не удалось загрузить АЗС</div>';
        });
        return;
      }
      renderStationList(stations);
      return;
    }

    // If query has 2+ chars — search entire DB via API
    if (ql.length >= 2) {
      showLoading();
      // Debounce not needed here (handler called only on input)
      const tgId = getTgId();
      let url = '/api/search?q=' + encodeURIComponent(ql);
      if (tgId) url += '&telegram_id=' + tgId;
      api(url).then(data => {
        hideLoading();
        const stations = data.stations || [];
        if (stations.length === 0) {
          list.innerHTML = `<div class="empty-mini">По запросу «${escape(ql)}» ничего не найдено.<br>Попробуйте изменить запрос.</div>`;
          return;
        }
        list.innerHTML = '';
        renderStationListInto(stations, list);
      }).catch(e => {
        hideLoading();
        showToast('Ошибка поиска: ' + e.message, 'error');
      });
      return;
    }
  }

  function renderStationList(stations) {
    renderStationListInto(stations, document.getElementById('station-picker-list'));
  }

  function renderStationListInto(stations, list) {
    if (!list) return;
    list.innerHTML = '';
    if (stations.length === 0) {
      list.innerHTML = '<div class="empty-mini">Нет АЗС</div>';
      return;
    }
    stations.forEach(s => {
      const op = s.operator || s.name || 'АЗС';
      const addr = s.address || s.city || '';
      const item = document.createElement('div');
      item.className = 'map-station-item';
      item.innerHTML = `
        <div class="map-station-icon">⛽</div>
        <div class="map-station-info">
          <div class="map-station-name">${escape(op)}</div>
          <div class="map-station-addr">${escape(addr)}</div>
        </div>
        <div class="map-station-arrow">›</div>
      `;
      item.addEventListener('click', () => {
        haptic('light');
        openReportSheet(s.id, op);
      });
      list.appendChild(item);
    });
  }

  function onStationPickerSearch(e) {
    clearTimeout(_stationPickerSearchTimer);
    const q = e.target.value;
    _stationPickerSearchTimer = setTimeout(() => {
      renderStationPicker(q);
    }, 300);
  }
  let _stationPickerSearchTimer = null;

  // ============= MAP =============
  function loadMap() {
    const container = document.getElementById('map-container');
    const list = document.getElementById('map-stations-list');
    if (!container || !list) return;

    if (!state.city) {
      container.innerHTML = '<div class="map-empty">📍 Выбери город на главной</div>';
      list.innerHTML = '';
      return;
    }

    // Show loading in map
    container.innerHTML = '<div class="map-empty">⏳ Загрузка карты...</div>';

    // Load stations
    const params = new URLSearchParams();
    params.set('city', state.city);
    if (state.fuel) params.set('fuel', state.fuel);
    params.set('limit', '50');
    api('/api/stations/by-city?' + params).then(data => {
      const stations = (data.stations || []).filter(s => s.lat && s.lon);
      if (stations.length === 0) {
        container.innerHTML = '<div class="map-empty">😔 Нет АЗС с координатами в этом городе</div>';
        list.innerHTML = '';
        return;
      }

      // Calculate center
      const centerLat = stations.reduce((sum, s) => sum + s.lat, 0) / stations.length;
      const centerLon = stations.reduce((sum, s) => sum + s.lon, 0) / stations.length;

      // Use OpenStreetMap embed (no API key needed)
      const bbox = calculateBbox(stations);
      const bboxStr = `${bbox.minLon},${bbox.minLat},${bbox.maxLon},${bbox.maxLat}`;
      const mapUrl = `https://www.openstreetmap.org/export/embed.html?bbox=${bboxStr}&layer=mapnik&marker=${centerLat},${centerLon}`;

      container.innerHTML = `<iframe src="${mapUrl}" loading="lazy" referrerpolicy="no-referrer-when-downgrade"></iframe>`;

      // Render list below
      list.innerHTML = '';
      stations.slice(0, 20).forEach(s => {
        const op = s.operator || s.name || 'АЗС';
        const addr = s.address || s.city || '';
        const item = document.createElement('div');
        item.className = 'map-station-item';
        item.innerHTML = `
          <div class="map-station-icon">⛽</div>
          <div class="map-station-info">
            <div class="map-station-name">${escape(op)}</div>
            <div class="map-station-addr">${escape(addr)}</div>
          </div>
          <div class="map-station-arrow">›</div>
        `;
        item.addEventListener('click', () => openStationDetail(s));
        list.appendChild(item);
      });
    }).catch(e => {
      container.innerHTML = `<div class="map-empty">⚠️ Ошибка: ${escape(e.message)}</div>`;
      list.innerHTML = '';
    });
  }

  function calculateBbox(stations) {
    let minLat = Infinity, maxLat = -Infinity;
    let minLon = Infinity, maxLon = -Infinity;
    stations.forEach(s => {
      if (s.lat < minLat) minLat = s.lat;
      if (s.lat > maxLat) maxLat = s.lat;
      if (s.lon < minLon) minLon = s.lon;
      if (s.lon > maxLon) maxLon = s.lon;
    });
    // Add padding
    const latPad = (maxLat - minLat) * 0.1 || 0.01;
    const lonPad = (maxLon - minLon) * 0.1 || 0.01;
    return {
      minLat: minLat - latPad,
      maxLat: maxLat + latPad,
      minLon: minLon - lonPad,
      maxLon: maxLon + lonPad,
    };
  }

  // ============= REPORT =============
  function openReportSheet(stationId, stationName) {
    state.reportSheet = {
      stationId: stationId || null,
      stationName: stationName || '',
      fuel: state.fuel || '92',
      available: true,
      price: null,
      queue: null,
    };
    dom.reportSheetStation.textContent = stationName || (state.stations.length > 0
      ? 'Выбери АЗС' : 'Сначала выбери АЗС');
    dom.reportPrice.value = '';
    dom.reportQueue.value = '';
    $$('.chip-fuel-sheet').forEach(c => c.classList.toggle('active', c.dataset.fuel === state.reportSheet.fuel));
    $$('.avail-btn').forEach(b => b.classList.toggle('active', String(b.dataset.avail) === String(state.reportSheet.available)));
    dom.reportSheet.hidden = false;
    haptic('light');
  }

  async function submitReport() {
    const { stationId, fuel, available, price, queue } = state.reportSheet;
    if (!stationId) {
      showToast('Сначала выбери АЗС', 'warning');
      return;
    }
    const tgId = getTgId();
    if (!tgId) {
      showToast('Не удалось определить пользователя', 'error');
      return;
    }
    showLoading();
    try {
      await api('/api/reports', {
        method: 'POST',
        body: JSON.stringify({
          station_id: stationId,
          fuel_type: fuel,
          available,
          price: price ? parseFloat(price) : null,
          queue_size: queue ? parseInt(queue) : null,
          telegram_id: tgId,
          first_name: tg?.initDataUnsafe?.user?.first_name || 'User',
        }),
      });
      closeSheet('report-sheet');
      hapticNotify('success');
      showToast('✅ Отчёт отправлен!', 'success');
      // Reload station detail
      if (state.selectedStation) openStationDetail(state.selectedStation);
      // Switch to home tab if no station detail
      if (!state.selectedStation) loadStations();
    } catch (e) {
      showToast('Ошибка: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  // ============= REVIEW =============
  function openReviewSheet(stationId, stationName) {
    state.reviewSheet = {
      stationId,
      stationName: stationName || '',
      fuel: '92',
      rating: 0,
      comment: '',
    };
    dom.reviewSheetStation.textContent = stationName || 'АЗС';
    dom.reviewComment.value = '';
    $$('.chip-review-fuel').forEach(c => c.classList.toggle('active', c.dataset.fuel === '92'));
    $$('.star').forEach(s => s.classList.remove('active', 'filled'));
    dom.ratingHint.textContent = 'Нажми на звезду';
    dom.reviewSheet.hidden = false;
    haptic('light');
  }

  async function submitReview() {
    const { stationId, fuel, rating, comment } = state.reviewSheet;
    if (!stationId) { showToast('Выбери АЗС', 'warning'); return; }
    if (rating === 0) { showToast('Поставь оценку', 'warning'); return; }
    const tgId = getTgId();
    if (!tgId) { showToast('Не удалось определить пользователя', 'error'); return; }

    showLoading();
    try {
      // Reviews use TG bot backend — we need a /api/reviews endpoint
      // For now use price-update as fallback or show error
      showToast('Отзывы пока можно оставить только в боте', 'info');
      closeSheet('review-sheet');
    } catch (e) {
      showToast('Ошибка: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  // ============= SUBSCRIBE =============
  async function subscribeStation(stationId) {
    const tgId = getTgId();
    if (!tgId) { showToast('Не удалось определить пользователя', 'error'); return; }
    showLoading();
    try {
      // We don't have a direct /api/subscribe endpoint — use bot
      showToast('Подпишись через бота: /subscribe', 'info');
    } catch (e) {
      showToast('Ошибка: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  // ============= PROFILE =============
  async function loadProfile() {
    const user = tg?.initDataUnsafe?.user;
    if (user) {
      const name = user.first_name + (user.last_name ? ' ' + user.last_name : '');
      dom.profileName.textContent = name;
      dom.profileId.textContent = 'ID: ' + user.id;
      dom.profileAvatar.textContent = user.first_name[0].toUpperCase();
      dom.profileBigAvatar.textContent = user.first_name[0].toUpperCase();
    } else if (platform.vk) {
      try {
        const userInfo = await window.vkBridge.send('VKWebAppGetUserInfo', {});
        dom.profileName.textContent = userInfo.first_name;
        dom.profileId.textContent = 'VK ID: ' + userInfo.id;
        state.vkUserId = userInfo.id;
        dom.profileAvatar.textContent = userInfo.first_name[0].toUpperCase();
        dom.profileBigAvatar.textContent = userInfo.first_name[0].toUpperCase();
      } catch (e) {
        dom.profileName.textContent = 'Гость';
        dom.profileId.textContent = '';
      }
    } else {
      dom.profileName.textContent = 'Гость';
      dom.profileId.textContent = '';
    }

    // Load stats
    try {
      const tgId = getTgId();
      if (tgId) {
        const stats = await api(`/api/stations?lat=0&lon=0&telegram_id=${tgId}`).catch(() => null);
        // No dedicated stats endpoint — use reports count via admin
      }
    } catch (e) {}
  }

  // ============= SEARCH =============
  let searchTimer = null;
  function onSearchInput() {
    const q = dom.searchInput.value.trim();
    dom.searchClear.hidden = q.length === 0;
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => doSearch(q), 350);
  }

  async function doSearch(q) {
    if (!q || q.length < 2) {
      if (state.city) {
        loadStations();
      } else {
        state.stations = [];
        renderStations();
      }
      return;
    }
    showLoading();
    try {
      // First try address search
      const params = new URLSearchParams();
      if (state.city) {
        params.set('city', state.city);
        const data = await api('/api/stations/by-city?' + params);
        state.stations = data.stations || [];
      } else {
        // General search
        const data = await api('/api/search?q=' + encodeURIComponent(q));
        state.stations = data.stations || [];
      }
      // Filter by query locally
      const ql = q.toLowerCase();
      state.stations = state.stations.filter(s => {
        const name = (s.name || '').toLowerCase();
        const op = (s.operator || '').toLowerCase();
        const addr = (s.address || '').toLowerCase();
        return name.includes(ql) || op.includes(ql) || addr.includes(ql);
      });
      dom.resultsTitle.textContent = q ? `Поиск: ${q}` : 'Результаты';
      renderStations();
    } catch (e) {
      showToast('Ошибка поиска: ' + e.message, 'error');
    } finally {
      hideLoading();
    }
  }

  // ============= CLOSE SHEET =============
  function closeSheet(id) {
    $('#' + id).hidden = true;
  }

  // ============= EVENT BINDING =============
  function bindEvents() {
    // Nav items
    $$('.nav-item').forEach(b => b.addEventListener('click', () => setTab(b.dataset.tab)));

    // Top buttons
    dom.citySelector.addEventListener('click', () => { haptic('light'); showCityPicker(); });
    dom.geoBtn.addEventListener('click', useGeo);
    dom.emergencyBtn.addEventListener('click', doEmergencySearch);
    $('#btn-profile').addEventListener('click', () => setTab('profile'));

    // Search
    dom.searchInput.addEventListener('input', onSearchInput);
    dom.searchClear.addEventListener('click', () => {
      dom.searchInput.value = '';
      dom.searchClear.hidden = true;
      loadStations();
    });

    // Fuel chips
    $$('.chip-fuel').forEach(c => {
      c.addEventListener('click', () => {
        $$('.chip-fuel').forEach(b => b.classList.remove('active'));
        c.classList.add('active');
        state.fuel = c.dataset.fuel;
        haptic('light');
        loadStations();
      });
    });

    // Report sheet
    $$('.chip-fuel-sheet').forEach(c => {
      c.addEventListener('click', () => {
        $$('.chip-fuel-sheet').forEach(b => b.classList.remove('active'));
        c.classList.add('active');
        state.reportSheet.fuel = c.dataset.fuel;
      });
    });
    $$('.avail-btn').forEach(b => {
      b.addEventListener('click', () => {
        $$('.avail-btn').forEach(x => x.classList.remove('active'));
        b.classList.add('active');
        state.reportSheet.available = b.dataset.avail === 'true';
        if (b.dataset.avail === 'queue') state.reportSheet.queue = 5;
        else state.reportSheet.queue = null;
      });
    });
    dom.reportPrice.addEventListener('input', e => state.reportSheet.price = e.target.value);
    dom.reportQueue.addEventListener('input', e => state.reportSheet.queue = e.target.value);
    $('#report-submit').addEventListener('click', submitReport);

    // Review sheet
    $$('.chip-review-fuel').forEach(c => {
      c.addEventListener('click', () => {
        $$('.chip-review-fuel').forEach(b => b.classList.remove('active'));
        c.classList.add('active');
        state.reviewSheet.fuel = c.dataset.fuel;
      });
    });
    $$('.star').forEach(s => {
      s.addEventListener('click', () => {
        const r = parseInt(s.dataset.rating);
        state.reviewSheet.rating = r;
        $$('.star').forEach(x => {
          const xr = parseInt(x.dataset.rating);
          x.classList.toggle('active', xr <= r);
        });
        const hints = ['', 'Ужасно', 'Плохо', 'Нормально', 'Хорошо', 'Отлично!'];
        dom.ratingHint.textContent = hints[r] || '';
        haptic('medium');
      });
    });
    dom.reviewComment.addEventListener('input', e => state.reviewSheet.comment = e.target.value);
    $('#review-submit').addEventListener('click', submitReview);

    // Sheet close
    $$('[data-action="close-sheet"]').forEach(el => {
      el.addEventListener('click', () => {
        closeSheet('report-sheet');
        closeSheet('review-sheet');
      });
    });

    // Back button in station picker goes to home
    $$('[data-action="back-to-report"]').forEach(el => {
      el.addEventListener('click', () => showScreen('home'));
    });

    // City picker
    dom.citySearch.addEventListener('input', () => renderCities(dom.citySearch.value));

    // Profile actions
    $('#btn-share').addEventListener('click', () => {
      haptic('light');
      const url = 'https://t.me/benzyn_ryadom';
      if (tg?.openTelegramLink) tg.openTelegramLink(url);
      else if (navigator.share) navigator.share({ title: 'Бензин рядом', url });
      else {
        navigator.clipboard?.writeText(url);
        showToast('Ссылка скопирована', 'success');
      }
    });
    $('#btn-donate').addEventListener('click', () => {
      haptic('light');
      if (tg?.openTelegramLink) tg.openTelegramLink('https://t.me/benzyn_ryadom?start=donate');
      else showToast('Перейди в бота: t.me/benzyn_ryadom', 'info');
    });
    $('#btn-help').addEventListener('click', () => {
      showToast('Бот: @benzyn_ryadom\nVK: vk.com/benzyn_ryadom', 'info');
    });
    $('#btn-premium').addEventListener('click', () => {
      haptic('medium');
      showToast('Premium пока в боте', 'info');
    });
  }

  // ============= INIT =============
  async function init() {
    bindEvents();

    // Load saved city
    try {
      const savedCity = localStorage.getItem('benzin_city');
      if (savedCity) {
        state.city = savedCity;
        state.cityRegion = localStorage.getItem('benzin_region') || '';
        dom.currentCity.textContent = savedCity;
      } else {
        dom.currentCity.textContent = 'Выбери город';
      }
    } catch (e) {
      dom.currentCity.textContent = 'Выбери город';
    }

    // Try to get user location for city auto-detect
    if (!state.city) {
      // Don't ask for location automatically; wait for user action
    }

    // Wait for VK bridge if VK
    if (platform.tg || platform.vk) {
      // Already detected
    }

    // Load stations
    if (state.city) {
      loadStations();
    } else {
      // Show welcome state immediately (no API needed)
      dom.stationsList.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">⛽</div>
          <div class="empty-title">Найди ближайшую АЗС</div>
          <div class="empty-subtitle">Выбери город наверху или нажми кнопку ниже</div>
          <button class="btn btn-primary" style="margin-top:16px; max-width:200px;" data-action="pick-city">📍 Выбрать город</button>
        </div>
      `;
      dom.emptyState.hidden = true;
      dom.resultsCount.textContent = '0';
      // Bind the button
      const btn = dom.stationsList.querySelector('[data-action="pick-city"]');
      if (btn) btn.addEventListener('click', () => showCityPicker());
    }
  }

  // Boot
  // Version check — force reload if old version is cached
  const APP_VERSION = '8';
  try {
    const stored = localStorage.getItem('benzin_app_version');
    if (stored && stored !== APP_VERSION) {
      console.log('App version changed, reloading...');
      localStorage.setItem('benzin_app_version', APP_VERSION);
      // Clear caches and force reload
      if ('caches' in window) {
        caches.keys().then(keys => keys.forEach(k => caches.delete(k)));
      }
      window.location.reload(true);
    } else {
      localStorage.setItem('benzin_app_version', APP_VERSION);
    }
  } catch (e) {
    // Ignore localStorage errors
  }

  window.addEventListener('error', (e) => {
    console.error('App error:', e.error);
    if (e.error && dom && dom.toast) {
      dom.toast.textContent = 'Ошибка: ' + (e.error.message || 'unknown');
      dom.toast.className = 'toast error';
      dom.toast.hidden = false;
    }
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
