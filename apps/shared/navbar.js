/* Shared Navbar JavaScript - include in all pages */
(function() {
  'use strict';

  // State
  var currentLang = 'en';
  var isMobileMenuOpen = false;
  var isLangDropdownOpen = false;

  // DOM elements (cached after DOMContentLoaded)
  var navbar = null;
  var hamburger = null;
  var mobileMenu = null;
  var mobileOverlay = null;
  var mobileClose = null;
  var langBtn = null;
  var langDropdown = null;
  var statusIndicator = null;
  var userArea = null;
  var mobileUserArea = null;
  var navLinks = [];
  var mobileNavLinks = [];

  // Initialize navbar
  function initNavbar() {
    navbar = document.querySelector('.navbar');
    if (!navbar) {
      console.warn('Navbar not found in DOM');
      return;
    }

    hamburger = document.getElementById('hamburger');
    mobileMenu = document.getElementById('mobile-menu');
    mobileOverlay = document.getElementById('mobile-overlay');
    mobileClose = document.getElementById('mobile-close');
    langBtn = document.getElementById('lang-btn');
    langDropdown = document.getElementById('lang-dropdown');
    statusIndicator = document.getElementById('system-status');
    userArea = document.getElementById('user-area');
    mobileUserArea = document.getElementById('mobile-user-area');

    // Cache nav links
    navLinks = Array.prototype.slice.call(document.querySelectorAll('.nav-link[data-page]'));
    mobileNavLinks = Array.prototype.slice.call(document.querySelectorAll('.mobile-nav-link[data-page]'));

    // Bind events
    bindEvents();

    // Set active page
    setActivePage();

    // Load system status
    checkSystemStatus();

    // Restore language preference
    loadLanguagePreference();

    // Update user area (page-specific)
    updateUserArea();
  }

  function bindEvents() {
    // Hamburger menu
    if (hamburger) {
      hamburger.addEventListener('click', toggleMobileMenu);
    }

    if (mobileClose) {
      mobileClose.addEventListener('click', closeMobileMenu);
    }

    if (mobileOverlay) {
      mobileOverlay.addEventListener('click', closeMobileMenu);
    }

    // Language dropdown
    if (langBtn) {
      langBtn.addEventListener('click', toggleLangDropdown);
    }

    if (langDropdown) {
      var options = langDropdown.querySelectorAll('.lang-option');
      options.forEach(function(opt) {
        opt.addEventListener('click', function() {
          setLanguage(opt.dataset.lang);
          closeLangDropdown();
        });
      });
    }

    // Close dropdown on outside click
    document.addEventListener('click', function(e) {
      if (langDropdown && langBtn && !langBtn.contains(e.target) && !langDropdown.contains(e.target)) {
        closeLangDropdown();
      }
    });

    // Keyboard navigation
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape') {
        closeMobileMenu();
        closeLangDropdown();
      }
    });

    // Mobile nav links
    mobileNavLinks.forEach(function(link) {
      link.addEventListener('click', function() {
        closeMobileMenu();
      });
    });

    // Window resize
    window.addEventListener('resize', handleResize);
  }

  function toggleMobileMenu() {
    isMobileMenuOpen = !isMobileMenuOpen;
    if (mobileMenu) mobileMenu.classList.toggle('open', isMobileMenuOpen);
    if (mobileOverlay) mobileOverlay.classList.toggle('open', isMobileMenuOpen);
    if (hamburger) hamburger.setAttribute('aria-expanded', isMobileMenuOpen);
    document.body.style.overflow = isMobileMenuOpen ? 'hidden' : '';
  }

  function closeMobileMenu() {
    isMobileMenuOpen = false;
    if (mobileMenu) mobileMenu.classList.remove('open');
    if (mobileOverlay) mobileOverlay.classList.remove('open');
    if (hamburger) hamburger.setAttribute('aria-expanded', 'false');
    document.body.style.overflow = '';
  }

  function toggleLangDropdown() {
    isLangDropdownOpen = !isLangDropdownOpen;
    if (langDropdown) langDropdown.classList.toggle('open', isLangDropdownOpen);
    if (langBtn) langBtn.setAttribute('aria-expanded', isLangDropdownOpen);
  }

  function closeLangDropdown() {
    isLangDropdownOpen = false;
    if (langDropdown) langDropdown.classList.remove('open');
    if (langBtn) langBtn.setAttribute('aria-expanded', 'false');
  }

  function setLanguage(lang) {
    if (lang === currentLang) return;
    currentLang = lang;

    // Update button
    if (langBtn) {
      var flag = lang === 'bn' ? '🇧🇩' : '🇬🇧';
      var code = lang === 'bn' ? 'BN' : 'EN';
      var flagEl = langBtn.querySelector('.lang-flag');
      var codeEl = langBtn.querySelector('.lang-code');
      if (flagEl) flagEl.textContent = flag;
      if (codeEl) codeEl.textContent = code;
    }

    // Update dropdown active state
    var options = langDropdown ? langDropdown.querySelectorAll('.lang-option') : [];
    options.forEach(function(opt) {
      opt.classList.toggle('active', opt.dataset.lang === lang);
    });

    // Save preference
    try {
      localStorage.setItem('tpp-lang', lang);
    } catch (e) {}

    // Dispatch custom event for page-specific handling
    document.dispatchEvent(new CustomEvent('tpp:langchange', { detail: { lang: lang } }));

    // Update page content if needed
    if (typeof window.updatePageLanguage === 'function') {
      window.updatePageLanguage(lang);
    }
  }

  function loadLanguagePreference() {
    try {
      var saved = localStorage.getItem('tpp-lang');
      if (saved && (saved === 'en' || saved === 'bn')) {
        setLanguage(saved);
      }
    } catch (e) {}
  }

  function setActivePage() {
    var path = window.location.pathname;
    var search = window.location.search;

    // Determine current page
    var page = 'home';
    if (path === '/teen-patti-pro' && search.includes('operator=demo')) {
      page = 'demo';
    } else if (path.startsWith('/admin')) {
      page = 'admin';
    } else if (path === '/api-docs') {
      page = 'api-docs';
    } else if (path === '/' || path === '') {
      page = 'home';
    }

    // Update desktop nav links
    navLinks.forEach(function(link) {
      var isActive = link.dataset.page === page;
      link.classList.toggle('active', isActive);
      link.setAttribute('aria-current', isActive ? 'page' : 'false');
    });

    // Update mobile nav links
    mobileNavLinks.forEach(function(link) {
      var isActive = link.dataset.page === page;
      link.classList.toggle('active', isActive);
      link.setAttribute('aria-current', isActive ? 'page' : 'false');
    });

    // Update mobile menu user area
    updateMobileUserArea();
  }

  function updateUserArea() {
    // This will be overridden by page-specific JS
    // Default: show login link if not authenticated
    if (userArea) {
      // Check for auth token in sessionStorage
      var token = sessionStorage.getItem('tpp.admin.key') ||
                  sessionStorage.getItem('dl-player');
      if (token && userArea) {
        userArea.innerHTML = '<span class="user-badge">Logged in</span>';
      } else if (userArea) {
        userArea.innerHTML = '<a href="/admin" class="nav-link" style="padding:6px 12px;font-size:.8rem">Sign In</a>';
      }
    }
  }

  function updateMobileUserArea() {
    if (mobileUserArea) {
      var token = sessionStorage.getItem('tpp.admin.key') ||
                  sessionStorage.getItem('dl-player');
      if (token) {
        mobileUserArea.innerHTML = '<div class="mobile-user-info">Logged in</div>';
      } else {
        mobileUserArea.innerHTML = '<a href="/admin" class="mobile-nav-link">Sign In</a>';
      }
    }
  }

  function checkSystemStatus() {
    if (!statusIndicator) return;

    fetch('/health', { cache: 'no-cache' })
      .then(function(res) {
        if (res.ok) {
          setStatus('healthy');
        } else {
          setStatus('down');
        }
      })
      .catch(function() {
        setStatus('down');
      });
  }

  function setStatus(state) {
    if (!statusIndicator) return;
    statusIndicator.className = 'status-indicator ' + state;
    var textEl = statusIndicator.querySelector('.status-text');
    if (textEl) textEl.textContent = state === 'healthy' ? 'Healthy' : 'Down';
  }

  function handleResize() {
    // Close mobile menu on resize to desktop
    if (window.innerWidth > 768 && isMobileMenuOpen) {
      closeMobileMenu();
    }
  }

  // Export for page-specific use
  window.TPPNavbar = {
    setActivePage: setActivePage,
    setLanguage: setLanguage,
    getCurrentLang: function() { return currentLang; },
    updateUserArea: updateUserArea,
    updateMobileUserArea: updateMobileUserArea,
    setStatus: setStatus,
    closeMobileMenu: closeMobileMenu,
    closeLangDropdown: closeLangDropdown
  };

  // Initialize on DOM ready
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initNavbar);
  } else {
    initNavbar();
  }
})();