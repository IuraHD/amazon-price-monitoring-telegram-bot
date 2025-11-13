# Amazon Price Monitor Bot 🛒

Professional Telegram bot for tracking Amazon product prices with intelligent folder organization and automatic notifications.

## ✨ Features

### 📊 Smart Price Tracking
- **Automatic price checks every 10 minutes** with instant notifications on changes
- **Discounted price detection** – always captures the lowest available price (deals, vouchers, AOD offers)
- **Manual refresh** – Update all prices on-demand with one click
- **Price history graphs** – Beautiful matplotlib charts showing trends over time
- **Multi-currency support** – Handles £, €, $, and other currencies with comma/dot decimals

### 🗂️ Folder Organization
- **Create custom folders** with names and emojis to organize your tracked products
- **Categorize products** during adding or move them later between folders
- **View totals per folder** – See total price across products in each category
- **Track uncategorized items** separately for flexibility

### 🔔 Notifications & Alerts
- **Real-time price change alerts** (configurable per product)
- **Minimum delta threshold** (£0.01) to avoid spam
- **Direction indicators** (↑/↓) with old price, new price, and delta
- **Auto-refresh graphs** sent with each notification

### 🎯 User-Friendly Interface
- **Product name and clickable URL** displayed in detail view
- **Beautiful inline keyboard menus** with emojis
- **Intuitive navigation** with smart back buttons
- **FSM-based flows** for smooth user experience
- **Clean chat management** (auto-deletes old graphs)

### ⚡ Performance
- **Async HTTP fetching** (aiohttp) with retry/backoff and 3 attempts
- **Smart marketplace canonicalization** to UK domain for consistency
- **Random user-agent rotation** to avoid blocks
- **Rate limiting** (60s per product) with minimal delta threshold
- **Strikethrough price filtering** – Ignores list prices, only captures actual prices
- **Graceful shutdown** handling

## 🚀 Quick Start

### Prerequisites
- Python 3.11+
- Telegram Bot Token (get from [@BotFather](https://t.me/botfather))

### Installation

```powershell
# Clone the repository
git clone https://github.com/yourusername/amazon-price-monitor-bot.git
cd amazon-price-monitor-bot

# Create virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1  # Windows PowerShell
# OR: source .venv/bin/activate  # Linux/Mac

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env and add your TELEGRAM_BOT_TOKEN

# Run the bot
python -m app.bot.bot
```

## 🎮 Usage

### Main Menu Options
- **➕ Add Product** – Start tracking a new Amazon product
- **📂 My Folders** – Manage your folder organization
- **📦 All Products** – View all tracked products with total price
- **🔄 Refresh All Prices** – Manually update all prices immediately

## 📱 Workflows

### Adding a Product
1. Click **Add Product**
2. Choose a folder (or create a new one, or select "No Folder")
3. Send the Amazon product URL
4. Receive confirmation with current price and price history graph

### Managing Folders
1. Click **My Folders**
2. View all folders with product counts
3. Create new folders with custom names and emojis
4. View products within each folder
5. Delete folders (products remain, moved to uncategorized)

### Product Management
When viewing a product:
- **🔔/🔕 Toggle Alerts** – Turn price change notifications on/off
- **🔄 Refresh Price** – Manually check for price updates
- **📂 Move to Folder** – Organize product into a different folder
- **🗑️ Remove** – Stop tracking this product

## ⚙️ Configuration

Create a `.env` file (copy from `.env.example`):

```env
TELEGRAM_BOT_TOKEN=123456:ABCDEFYourBotToken

# Optional settings:
TELEGRAM_PRIMARY_CHAT_ID=123456789  # Restrict bot to specific chat (leave empty for no restriction)
PRICE_REFRESH_MINUTES=10  # Background price check interval (default: 10 minutes)
```

## 🗄️ Architecture

### Project Structure
```
app/
├── bot/
│   ├── bot.py          # Main router and handlers
│   ├── keyboards.py    # Inline keyboard builders
│   └── states.py       # FSM states
├── core/
│   ├── config.py       # Environment configuration
│   ├── db.py          # SQLite connection and schema
│   ├── folders.py     # Folder CRUD operations
│   └── products.py    # Product and price history operations
├── utils/
│   ├── fetch.py       # Amazon scraping (price, title, ASIN)
│   └── graph.py       # Price history chart generation
└── data/
    └── tracker.db     # SQLite database (auto-created)
```

### Database Schema
- **folders** – User-created categories with emoji customization
- **products** – Tracked items with ASIN, URL, title, price, folder assignment
- **price_history** – Timestamped price snapshots for trending

## 🛠️ Key Technologies
- **aiogram 3.x** – Modern async Telegram Bot framework
- **aiohttp** – Async HTTP client for fast concurrent fetching
- **BeautifulSoup4** – HTML parsing for Amazon scraping
- **matplotlib** – Price trend graph generation
- **SQLite** – Lightweight embedded database
- **structlog** – Structured logging for production monitoring

## 📝 License

MIT License - feel free to use and modify!

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 🐛 Issues

Found a bug or have a feature request? Please open an issue on GitHub.

---

Built with ❤️ for smart Amazon shoppers

## Extensibility Ideas
- Target price alerts per product
- Price drop percentage thresholds
- Export price history to CSV
- Webhook mode for better scalability
- Admin dashboard with statistics
- Share folders between users

## License
Personal use.
