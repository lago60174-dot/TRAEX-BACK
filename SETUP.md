# 🚀 FOREX TRADING BACKEND — GUIDE DE DÉPLOIEMENT COMPLET

## ARCHITECTURE

```
Frontend (React/Vite)          Backend (FastAPI)           Broker
   Vercel / Netlify     ───▶   Render.com           ───▶   OANDA v20
         │                          │
         │                     Supabase (PostgreSQL)
         │                          │
         └──── Push Notifications ◀─┘
                (Edge Function + VAPID)
```

---

## ÉTAPE 1 — SUPABASE

### 1.1 Créer le projet
1. Va sur https://app.supabase.com → New project
2. Donne un nom, choisis la région (Europe West)
3. Note : `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_ANON_KEY`

### 1.2 Créer les tables
1. Va dans **SQL Editor**
2. Colle et exécute le fichier `supabase/migration.sql`
3. Configure les variables pour les triggers :
```sql
ALTER DATABASE postgres SET app.supabase_url = 'https://TON_PROJET.supabase.co';
ALTER DATABASE postgres SET app.supabase_anon_key = 'TON_ANON_KEY';
```

### 1.3 Déployer la Edge Function
```bash
# Installer Supabase CLI
npm install -g supabase

# Login
supabase login

# Link ton projet
supabase link --project-ref TON_PROJECT_REF

# Définir les secrets VAPID
supabase secrets set VAPID_PUBLIC_KEY=ta_cle_publique
supabase secrets set VAPID_PRIVATE_KEY=ta_cle_privee
supabase secrets set VAPID_SUBJECT=mailto:ton@email.com

# Déployer la function
supabase functions deploy send-push-notification
```

---

## ÉTAPE 2 — CLÉS VAPID

Génère tes clés VAPID (une seule fois) :

**Option A — En ligne :** https://vapidkeys.com

**Option B — Node.js :**
```bash
npx web-push generate-vapid-keys
```

Résultat :
```
Public Key:  BNxx...  ← dans VITE_VAPID_PUBLIC_KEY + supabase secrets
Private Key: xxx...   ← dans supabase secrets UNIQUEMENT
```

---

## ÉTAPE 3 — OANDA

1. Crée un compte sur https://www.oanda.com
2. **Pour tester :** Demo Account → génère une API key
3. **Pour l'argent réel :** Live Account → génère une API key
4. Note : `OANDA_API_KEY` et `OANDA_ACCOUNT_ID`

**⚠️ Commence TOUJOURS avec `OANDA_ENVIRONMENT=practice` avant de passer en `live`**

---

## ÉTAPE 4 — BACKEND SUR RENDER

### 4.1 Générer le hash du mot de passe admin
```bash
python3 -c "from passlib.context import CryptContext; print(CryptContext(['bcrypt']).hash('TON_MOT_DE_PASSE'))"
```

### 4.2 Déployer
1. Push ton code sur GitHub
2. Va sur https://render.com → New Web Service
3. Connecte ton repo
4. Build command : `pip install -r requirements.txt`
5. Start command : `uvicorn app.main:app --host 0.0.0.0 --port $PORT`

### 4.3 Variables d'environnement sur Render
```
APP_ENV=production
OANDA_API_KEY=...
OANDA_ACCOUNT_ID=...
OANDA_ENVIRONMENT=practice  (changer en "live" quand prêt)
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
SUPABASE_ANON_KEY=...
JWT_SECRET=  (Render peut générer automatiquement)
ADMIN_PASSWORD_HASH=  (hash généré à l'étape 4.1)
MONITOR_INTERVAL_SECONDS=30
LOG_LEVEL=INFO
```

---

## ÉTAPE 5 — FRONTEND

### 5.1 Configuration
```bash
cd frontend
cp .env.example .env.local
# Édite .env.local :
# VITE_API_URL=https://ton-backend.onrender.com
# VITE_VAPID_PUBLIC_KEY=ta_cle_vapid_publique
```

### 5.2 Déployer sur Vercel
```bash
npm install -g vercel
vercel deploy
```

Ou Netlify :
```bash
npm run build
# Drag & drop le dossier dist/ sur netlify.com
```

### 5.3 CORS Backend
Dans `app/main.py`, remplace l'URL CORS :
```python
allow_origins=["https://ton-frontend.vercel.app"]
```

---

## ÉTAPE 6 — ACTIVER LES NOTIFICATIONS PUSH

1. Ouvre le frontend dans ton navigateur
2. Va dans **Notifications**
3. Clique **"Activer"** → accepte la permission
4. Clique **"Test"** → tu devrais recevoir une notification
5. C'est fait — toutes les alertes sont actives

---

## ÉTAPE 7 — PASSER EN ARGENT RÉEL

Checklist avant de switcher en live :

- [ ] Testé 2-4 semaines en paper trading (practice)
- [ ] Statistiques positives (win rate > 50%, PnL > 0)
- [ ] Toutes les notifications reçues correctement
- [ ] Risk management vérifié (limits, daily loss)
- [ ] Compte OANDA Live créé et vérifié (KYC)
- [ ] Changer `OANDA_ENVIRONMENT=live` sur Render
- [ ] Redéployer le service

**⚠️ AVERTISSEMENT : Le trading Forex comporte des risques de pertes. Ne trade qu'avec de l'argent que tu peux te permettre de perdre.**

---

## DÉVELOPPEMENT LOCAL

```bash
# Backend
cd forex-backend
python -m venv venv
source venv/bin/activate  # ou venv\Scripts\activate sur Windows
pip install -r requirements.txt
cp .env.example .env
# Édite .env avec tes vraies valeurs
uvicorn app.main:app --reload --port 8000

# Frontend (autre terminal)
cd frontend
npm install
cp .env.example .env.local
npm run dev
# → http://localhost:5173
```

---

## STRUCTURE DU PROJET

```
forex-backend/
├── app/
│   ├── main.py                    ← FastAPI app entry
│   ├── config.py                  ← Settings (Pydantic)
│   ├── api/routes/
│   │   ├── auth.py                ← POST /auth/login
│   │   ├── account.py             ← GET /account
│   │   ├── trades.py              ← CRUD trades
│   │   ├── strategy.py            ← POST /strategy/run
│   │   ├── risk.py                ← GET/POST /risk
│   │   └── notifications.py      ← Push subscription
│   ├── core/
│   │   ├── risk_engine.py         ← Garde-fou risque
│   │   ├── execution_engine.py    ← Ouverture/fermeture
│   │   ├── portfolio_manager.py   ← Compte OANDA
│   │   └── strategies/
│   │       ├── ema_pullback.py    ← Stratégie 1
│   │       ├── rsi_mean_reversion.py ← Stratégie 2
│   │       └── breakout_atr.py   ← Stratégie 3
│   ├── data/
│   │   ├── oanda_client.py        ← Client OANDA v20
│   │   └── database.py            ← Client Supabase
│   ├── services/
│   │   ├── scheduler.py           ← Jobs background
│   │   ├── notifications.py       ← Service push
│   │   └── auth.py                ← JWT
│   └── models/schemas.py          ← Tous les types
├── supabase/
│   ├── migration.sql              ← Tables + triggers DB
│   └── functions/
│       └── send-push-notification/
│           └── index.ts           ← Edge Function Deno
├── frontend/
│   ├── src/App.jsx                ← Dashboard React complet
│   ├── public/sw.js               ← Service Worker push
│   └── package.json
├── render.yaml                    ← Config déploiement Render
├── requirements.txt
└── .env.example
```

---

## ENDPOINTS API

| Méthode | Route | Description |
|---------|-------|-------------|
| POST | /auth/login | Connexion |
| GET | /account | Balance + equity |
| GET | /account/balance | Balance rapide |
| GET | /trades/open | Trades ouverts |
| GET | /trades/history | Historique |
| GET | /trades/stats/summary | Stats agrégées |
| POST | /trades/open | Ouvrir un trade |
| POST | /trades/close | Fermer un trade |
| POST | /strategy/run | Analyser + signal |
| GET | /strategy/status | Config stratégies |
| GET | /risk/status | État risque complet |
| GET | /risk/settings | Paramètres risque |
| POST | /risk/settings | Modifier risque |
| POST | /notifications/subscribe | Enregistrer device |
| GET | /notifications/status | Subscription active? |
| POST | /notifications/test | Test notification |
| GET | /notifications/logs | Historique notifs |
| GET | /health | Health check |
