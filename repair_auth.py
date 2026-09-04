from pathlib import Path
import re

p = Path('index.html')
s = p.read_text(encoding='utf-8')

s = s.replace('function function centralStatCard', 'function centralStatCard', 1)

login = '''        async function handleLogin(e) {
            e.preventDefault();
            const errorEl = document.getElementById('loginError');
            const button = document.querySelector('#loginForm button[type="submit"]');
            const emailEl = document.getElementById('loginEmail');
            const passwordEl = document.getElementById('loginPassword');
            if (!errorEl || !emailEl || !passwordEl) return;
            errorEl.textContent = '';
            const email = emailEl.value.trim().toLowerCase();
            const password = passwordEl.value;
            if (!email || !password) { errorEl.textContent = 'Please enter your email and password.'; return; }
            if (button) { button.disabled = true; button.dataset.originalText = button.textContent.trim(); button.textContent = 'Logging in...'; }
            try {
                const res = await fetch('/api/auth/login', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: JSON.stringify({ email, password })
                });
                const contentType = res.headers.get('content-type') || '';
                const data = contentType.includes('application/json') ? await res.json().catch(() => ({})) : {};
                if (!res.ok) {
                    let detail = data.detail;
                    if (Array.isArray(detail)) detail = detail.map(x => x && x.msg ? x.msg : String(x)).join(', ');
                    errorEl.textContent = detail || `Login failed (HTTP ${res.status}).`;
                    return;
                }
                if (!data || typeof data !== 'object' || !data.institute_name) {
                    errorEl.textContent = 'The server returned an invalid login response. Please refresh and try again.';
                    return;
                }
                completeAuth(data);
            } catch (err) {
                console.error('Algorithmic login request failed:', err);
                errorEl.textContent = 'Unable to reach the login server. Please refresh the page and try again.';
            } finally {
                if (button) { button.disabled = false; button.textContent = button.dataset.originalText || 'Log In'; }
            }
        }'''

signup = '''        async function handleSignup(e) {
            e.preventDefault();
            const errorEl = document.getElementById('signupError');
            const button = document.querySelector('#signupForm button[type="submit"]');
            errorEl.textContent = '';
            const institute_name = document.getElementById('signupInstitute').value.trim();
            const full_name = document.getElementById('signupName').value.trim();
            const email = document.getElementById('signupEmail').value.trim().toLowerCase();
            const password = document.getElementById('signupPassword').value;
            if (!institute_name || !full_name || !email || !password) { errorEl.textContent = 'Please complete all fields.'; return; }
            if (password.length < 8) { errorEl.textContent = 'Password must be at least 8 characters.'; return; }
            if (button) { button.disabled = true; button.dataset.originalText = button.textContent.trim(); button.textContent = 'Creating account...'; }
            try {
                const res = await fetch('/api/auth/signup', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
                    body: JSON.stringify({ institute_name, full_name, email, password })
                });
                const contentType = res.headers.get('content-type') || '';
                const data = contentType.includes('application/json') ? await res.json().catch(() => ({})) : {};
                if (!res.ok) {
                    let detail = data.detail;
                    if (Array.isArray(detail)) detail = detail.map(x => x && x.msg ? x.msg : String(x)).join(', ');
                    errorEl.textContent = detail || `Signup failed (HTTP ${res.status}).`;
                    return;
                }
                if (!data || typeof data !== 'object' || !data.institute_name) {
                    errorEl.textContent = 'The server returned an invalid signup response. Please refresh and try again.';
                    return;
                }
                completeAuth(data);
            } catch (err) {
                console.error('Algorithmic signup request failed:', err);
                errorEl.textContent = 'Unable to reach the signup server. Please refresh the page and try again.';
            } finally {
                if (button) { button.disabled = false; button.textContent = button.dataset.originalText || 'Create Account'; }
            }
        }'''

pattern_login = r'        async function handleLogin\(e\) \{.*?\n        \}'
pattern_signup = r'        async function handleSignup\(e\) \{.*?\n        \}'

s, n1 = re.subn(pattern_login, login, s, count=1, flags=re.S)
s, n2 = re.subn(pattern_signup, signup, s, count=1, flags=re.S)

if n1 != 1 or n2 != 1:
    raise SystemExit(f'Could not locate auth handlers: login={n1}, signup={n2}')

p.write_text(s, encoding='utf-8')
print('Authentication handlers repaired.')
