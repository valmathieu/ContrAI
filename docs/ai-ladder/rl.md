# Reinforcement learning

Self-play training for stronger agents. Not yet implemented.

Candidate algorithms — discuss tradeoffs honestly:
- **MCTS / IS-MCTS / Determinized UCT** — natural fit for imperfect-information trick games
- **PPO** — actor-critic baseline
- **AlphaZero-style** — needs adaptation for imperfect information
- **NFSP** (Neural Fictitious Self-Play) — explicitly handles imperfect info

**Explainability:** MCTS-family — expose visit counts, win-rate estimates, principal variation.

> TODO: algorithm selection; training infra; evaluation protocol (ELO/TrueSkill, head-to-head, statistical significance).
