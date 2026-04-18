# ContrAI Scraper 🕵️‍♂️

This repository contains the **data collection** module for the [ContrAI](link-to-your-main-repo) project.

It is a "passive" bot using **Playwright** to connect to a Belote Contrée gaming platform, observe ongoing games, and save the game state as raw data.

## 🏗 Architecture

This project follows the **Data Decoupling** principle:

* **This bot is "dumb":** It does not know the rules of the game. It does not validate moves. It strictly takes "snapshots" of the game table.
* **Output Format:** Locally stored raw JSON files.
* **Processing:** Parsing, validation, and AI training are handled by the main `ContrAI-Core` repository.

## 🛠 Prerequisites

* Python
* Playwright

## 🚀 Installation

Clone the repository:

```bash
git clone [https://github.com/your-username/contrai-scraper.git](https://github.com/your-username/contrai-scraper.git)
cd contrai-scrapergit clone [https://github.com/your-username/contrai-scraper.git](https://github.com/your-username/contrai-scraper.git)
cd contrai-scraper
```




