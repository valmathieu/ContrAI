# 🃏 Contrée Probabilities Dashboard

![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)
![Streamlit](https://img.shields.io/badge/Streamlit-FF4B4B.svg?logo=streamlit&logoColor=white)
![uv](https://img.shields.io/badge/uv-Lightning_Fast-purple.svg)
![Status](https://img.shields.io/badge/Status-Work_in_Progress-orange.svg)

A modern, interactive web application designed to bring mathematical rigor to **la contrée** (a highly strategic variant of the French card game Belote). 

This dashboard replaces "gut feeling" with pure statistics. By inputting your 8-card opening hand, the engine uses combinatorics and hypergeometric distributions to evaluate your optimal opening bid, assess your partner's likely support, and calculate the probability of opponent threats.

## 🎯 Core Features

### 🚀 Current Capabilities

* **Interactive Hand Selection:** A UI to select your 8 starting cards.
* **Bidding Decision Engine:** An automated diagnostic tool that cross-references your hand against a proven tactical truth table (evaluating trumps, outside Aces, and expected tricks) to suggest the optimal opening bid (80 to 160).
* **Partner Support (Raise) Odds:** Calculates the exact probability of your partner holding critical missing cards (e.g., the missing outside Ace or the top trump 9/Jack).
* **Threat Assessment:** Evaluates defensive risks, such as the exact probability of an opponent holding a "Third Ace" in a suit where you are void.
* **Visual Distribution:** Beautiful, interactive Plotly charts showing the most likely distribution of remaining suits among the other three players.

### 🔮 Future Developments (Roadmap)

* **Monte Carlo Trick Simulator:** Simulating thousands of random play-outs to estimate the Expected Value (EV) of tricks won.
* **Double/Redouble Risk Calculator:** Assessing the mathematical risk of being doubled based on opponent distribution.
* **Session Tracker:** Logging hands and bidding accuracy over time to analyze player performance.

## 🧮 The Mathematics (Under the Hood)

The core engine relies on the **Hypergeometric Distribution** (sampling without replacement). 
Since a contrée deck has 32 cards and you hold 8, there are exactly 24 unknown cards split into three 8-card hands. The probability of any specific player holding exactly $k$ target cards from a pool of $K$ available target cards is calculated using standard combinatorial logic:

$$
P(X = k) = \frac{\binom{K}{k} \binom{24 - K}{8 - k}}{\binom{24}{8}}
$$

## 🛠️ Tech Stack

* **Language:** Python 3.12+
* **Framework:** [Streamlit](https://streamlit.io/) (for the reactive UI/Dashboard)
* **Visualizations:** [Plotly](https://plotly.com/python/) (Interactive charting)
* **Data Manipulation:** Pandas
* **Package Management:** [uv](https://github.com/astral-sh/uv) (by Astral)

## 🏎️ Getting Started

This project uses `uv` for lightning-fast dependency management and virtual environments.

**1. Clone the repository**

```powershell
git clone [https://github.com/valmathieu/Contree-Probabilites.git](https://github.com/valmathieu/Contree-Probabilites.git)
cd Contree-Probabilites
```

**2. Sync the environment and install dependencies**

PowerShell

```
uv sync
```

*(If you haven't initialized the `pyproject.toml` yet, use `uv add streamlit plotly pandas`).*

**3. Run the application**

PowerShell

```
uv run streamlit run app.py
```

The dashboard will automatically open in your default web browser at `http://localhost:8501`.

## 🤝 Contributing

Contributions, issues, and feature requests are welcome! Feel free to check the [issues page](https://www.google.com/search?q=https://github.com/valmathieu/Contree-Probabilites/issues).

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.
