# 🤖 Bitget Quant Bot: Strategia "3 Corvi Rossi"

Questo è un bot di trading quantitativo automatizzato sviluppato in Python per l'exchange **Bitget** (Mercato USDT-FUTURES). Il bot scansiona costantemente il mercato alla ricerca di specifici pattern di panico ribassista per piazzare una rete di acquisto DCA (Dollar Cost Averaging) e catturare il rimbalzo fisiologico.

---

## 📊 La Strategia
La logica del bot si basa su parametri rigidamente testati statisticamente su un backtest di 2 anni:

1. **Identificazione del Panico:** Il bot analizza le ultime 3 candele chiuse (Timeframe: 15 minuti). Se sono **tutte e tre rosse consecutive** e il prezzo è sceso di almeno l'**1.5%** rispetto al picco massimo (High-Water Mark) toccato in quell'arco di tempo, il segnale si attiva.
2. **Ingresso a Rete (DCA):** Il bot piazza istantaneamente una rete di 3 ordini Limite (Batch Orders) sotto al prezzo di innesco:
   * 30% del capitale a -0.5%
   * 30% del capitale a -1.5%
   * 40% del capitale a -2.5%
3. **Uscita Rapida:** Ogni ordine ha un Take Profit preimpostato all'**1.2%** e uno Stop Loss protettivo al **6.0%**.
4. **Timeout di Sicurezza:** Se la posizione non colpisce né TP né SL entro **4 ore** (16 candele), il bot chiude forzatamente a mercato per evitare di rimanere bloccato in un trend avverso.

---

## 🛠️ Requisiti di Sistema e Installazione
Il bot è progettato per essere estremamente leggero, ideale per VPS Linux (es. AWS EC2 Nano) con risorse limitate (<500MB RAM).

**1. Installa le dipendenze Python necessarie:**
```bash
pip install pandas requests python-dotenv
```
2. Crea il file delle variabili d'ambiente (.env):
3. Crea un file chiamato .env nella stessa cartella di bot.py e inserisci le tue credenziali API di Bitget:
Snippet di codice
```
API_KEY=inserisci_qui_la_tua_api_key
SECRET_KEY=inserisci_qui_il_tuo_secret_key
PASSPHRASE=inserisci_qui_la_tua_passphrase
```
## ⚙️ Configurazione Parametri (`bot.py`)

All'interno del file principale, puoi modificare le costanti operative in cima allo script:

| Variabile | Valore Default | Descrizione |
| :--- | :--- | :--- |
| `SYMBOL` | `SOLUSDT` | La coppia di trading su cui operare. |
| `TIMEFRAME` | `15m` | Risoluzione delle candele. |
| `CAPITAL_TO_USE` | `100.0` | Capitale totale (in USDT) da dividere per la rete DCA. |
| `CRASH_DROP_PCT` | `0.015` | Drop cumulativo minimo (1.5%) per attivare il setup. |
| `TAKE_PROFIT_PCT` | `0.012` | Target di profitto (1.2%). |
| `STOP_LOSS_PCT` | `0.060` | Stop loss massimo di emergenza (6.0%). |
| `MAX_HOLD_CANDLES` | `16` | Candele massime di attesa prima della chiusura forzata. |

---

## 🏗️ Architettura del Codice (Clean Architecture)

Il codice è suddiviso in moduli funzionali per facilitare la lettura e la manutenzione:

* **Moduli API:** Gestiscono la firma HMAC-SHA256 e le chiamate di rete.
* **Moduli Dati:** Scaricano lo storico (`get_market_data`) e verificano la strategia (`check_flash_crash_signal`).
* **Modulo Ordini:** Invia le operazioni a mercato in formato Batch.
* **Modulo Dashboard:** Estrae i dati del conto (`get_account_balance`, `get_position_info`) e li renderizza a schermo separatamente dalla logica operativa.

---

## 🚀 Come Eseguire il Bot in Produzione

Per avviare il bot su un server senza che si spenga quando chiudi la finestra del terminale, usa `screen`:

1.  **Avvia una sessione in background:**
    ```bash
    screen -S quantbot
    ```
2.  **Lancia lo script** (usa `-u` per forzare l'output live sul terminale):
    ```bash
    python3 -u bot.py
    ```
3.  **Sganciati dalla sessione (Detach):**
    Premi `CTRL + A` seguito dal tasto `D`. Ora puoi chiudere il terminale in sicurezza.
4.  **Per riaprire la dashboard:**
    ```bash
    screen -r quantbot
    ```

---

## ⚠️ Sicurezza e Avvertenze

* **Permessi API:** Assicurati che l'API Key generata su Bitget abbia permessi **esclusivi** per il trading "USDT-M Futures". **NON abilitare mai i permessi di prelievo (Withdrawal)**.
* **Anti-Disconnessione:** Il bot possiede una variabile di sicurezza (`pos_api_success`) che gli impedisce di resettare la strategia se le API di Bitget non rispondono correttamente, prevenendo chiusure di posizione fantasma.
* **Disclaimer:** Il trading algoritmico comporta rischi di mercato. Non far girare il bot con fondi superiori a quelli impostati nel parametro `CAPITAL_TO_USE` senza aver compreso pienamente il meccanismo della griglia.
