/**
 * Main application JS for Uzhhorod Lyceum #3
 * Includes Modern Seamless Page Transitions (SPA-like multi-page navigation)
 */

document.addEventListener('DOMContentLoaded', () => {
    // Auto dismiss flash alerts after 5 seconds
    initFlashAlerts();
    // SPA-like smooth transitions between pages
    initInstantNavigation();
});

function initFlashAlerts() {
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(alert => {
        setTimeout(() => {
            alert.style.transition = 'opacity 0.5s ease';
            alert.style.opacity = '0';
            setTimeout(() => alert.remove(), 500);
        }, 5000);
    });
}

/**
 * Modern SPA-like Navigation (HTML5 PushState + Fetch + DOM morph)
 * Changes pages smoothly without full page reload.
 */
function initInstantNavigation() {
    if (window._instantNavInitialized) return;
    window._instantNavInitialized = true;

    // Check if browser supports modern APIs
    if (!window.fetch || !window.history || !window.DOMParser) return;

    // Intercept clicks on links
    document.addEventListener('click', (e) => {
        const link = e.target.closest('a');
        if (!link) return;

        const href = link.getAttribute('href');
        if (!href) return;

        // Skip non-navigational links
        if (
            href.startsWith('#') ||
            href.startsWith('javascript:') ||
            href.startsWith('mailto:') ||
            href.startsWith('tel:') ||
            link.getAttribute('target') === '_blank' ||
            link.hasAttribute('download') ||
            link.dataset.noSpa !== undefined
        ) {
            return;
        }

        // Only handle internal links
        let url;
        try {
            url = new URL(href, window.location.origin);
        } catch (_) {
            return;
        }
        if (url.origin !== window.location.origin) return;

        // Never intercept active test-taking session or logout
        if (
            url.pathname.includes('/take') ||
            url.pathname.includes('/logout') ||
            window.location.pathname.includes('/take')
        ) {
            return;
        }

        e.preventDefault();
        loadPage(url.href, true);
    });

    window.addEventListener('popstate', () => {
        loadPage(window.location.href, false);
    });
}

let _isNavigating = false;

async function loadPage(urlStr, pushHistory) {
    if (_isNavigating) return;
    _isNavigating = true;

    // Show subtle top loading indicator
    let bar = document.getElementById('spa-progress-bar');
    if (!bar) {
        bar = document.createElement('div');
        bar.id = 'spa-progress-bar';
        bar.style.cssText = 'position:fixed;top:0;left:0;height:3px;background:linear-gradient(90deg,#4f46e5,#3b82f6,#10b981);z-index:99999;width:0%;transition:width 0.2s ease, opacity 0.3s ease;';
        document.body.appendChild(bar);
    }
    bar.style.opacity = '1';
    bar.style.width = '30%';

    try {
        const resp = await fetch(urlStr, {
            headers: { 'X-Requested-With': 'Spa-Navigate' },
            credentials: 'same-origin'
        });

        bar.style.width = '70%';

        if (!resp.ok) {
            // Fallback to normal navigation on error
            window.location.href = urlStr;
            return;
        }

        const htmlText = await resp.text();
        const parser = new DOMParser();
        const newDoc = parser.parseFromString(htmlText, 'text/html');

        const newMain = newDoc.querySelector('main.main-content');
        const currentMain = document.querySelector('main.main-content');

        if (!newMain || !currentMain) {
            window.location.href = urlStr;
            return;
        }

        // Update Title
        if (newDoc.title) {
            document.title = newDoc.title;
        }

        // Smooth fade transition
        currentMain.style.transition = 'opacity 0.12s ease';
        currentMain.style.opacity = '0.3';

        setTimeout(() => {
            currentMain.innerHTML = newMain.innerHTML;
            currentMain.style.opacity = '1';

            // Update active menu links
            updateActiveMenu(urlStr);

            if (pushHistory) {
                history.pushState(null, '', urlStr);
            }
            window.scrollTo({ top: 0, behavior: 'instant' });

            // Re-execute scripts inside main content if any
            executeScripts(currentMain);

            // Re-render KaTeX math if loaded
            if (window.renderMathInElement) {
                try {
                    renderMathInElement(currentMain, {
                        delimiters: [
                            {left: '$$', right: '$$', display: true},
                            {left: '$', right: '$', display: false},
                            {left: '\\(', right: '\\)', display: false},
                            {left: '\\[', right: '\\]', display: true}
                        ],
                        strict: false,
                        throwOnError: false
                    });
                } catch(_) {}
            }

            initFlashAlerts();

            bar.style.width = '100%';
            setTimeout(() => {
                bar.style.opacity = '0';
                setTimeout(() => { bar.style.width = '0%'; }, 300);
            }, 150);

            _isNavigating = false;
        }, 120);

    } catch (err) {
        console.warn('SPA navigation failed, falling back to full reload:', err);
        window.location.href = urlStr;
    }
}

function updateActiveMenu(urlStr) {
    try {
        const u = new URL(urlStr, window.location.origin);
        document.querySelectorAll('nav a').forEach(a => {
            try {
                const aUrl = new URL(a.getAttribute('href'), window.location.origin);
                if (aUrl.pathname === u.pathname) {
                    a.classList.add('active');
                } else if (aUrl.pathname !== '/') {
                    a.classList.remove('active');
                }
            } catch (_) {}
        });
    } catch (_) {}
}

function executeScripts(container) {
    const scripts = container.querySelectorAll('script');
    scripts.forEach(oldScript => {
        const newScript = document.createElement('script');
        Array.from(oldScript.attributes).forEach(attr => {
            newScript.setAttribute(attr.name, attr.value);
        });
        newScript.textContent = oldScript.textContent;
        oldScript.parentNode.replaceChild(newScript, oldScript);
    });
}
