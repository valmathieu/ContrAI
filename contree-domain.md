# Contrée — Domain Knowledge

> **Scope.** This document captures the *game* of Contrée independent of any
> software implementation. It is the canonical reference for rules,
> terminology, and community conventions used across the ContrAI project. For
> *what the engine does*, see `Specs_logicielles.md` and `Specs_fonctionelles.md`;
> for *how the AI reasons*, see the AI-family docs alongside their
> implementations.

---

## 1. Overview

Contrée is a French trick-taking card game for four players in two fixed partnerships. It is a member of the Jass family (Klaverjassen → Belote → Contrée) and inherits its bidding mechanism from Bridge: each round, the two teams bid against each other for the right to choose the trump suit and to commit to a points contract.

Play nests on three levels:

- **Round** — one deal + auction + 8 tricks + scoring.
- **Game** — a sequence of rounds, played to a **target score**: 500, 1000, 1500, **2000** (default), 3000, 4000 or 5000.
- **Match** — a sequence of games, won by the first team to take `N` of them (**1** by default).

Each round runs through four phases:

1. **Deal**
2. **Bidding**
3. **Card play** — 8 tricks
4. **Scoring**

The first team to reach the target score at the end of a round wins the game (§8); the first team to win the agreed number of games wins the match. With the default of a single winning game, match and game coincide — both settings are catalogued in §9.

---

## 2. Setup

- **Players.** Exactly 4.
- **Teams.** Two fixed pairs, partners seated opposite each other. By convention we call them **North–South** and **East–West** (N–S vs E–W), as in Bridge.
- **Deck.** 32 cards: 7, 8, 9, 10, Jack, Queen, King, Ace in each of four suits (♠ ♥ ♦ ♣).
- **Turn order.** **Table option — turn direction** (anticlockwise by default, §9): the player to the **right** of the current actor plays next. Tables may agree to play clockwise instead. The direction is a single setting governing dealing, bidding order, the lead and dealer rotation together; every directional rule in this document mirrors accordingly.
- **Dealer rotation.** Follows the turn direction — each new round, the dealer is the next player along, to the right of the previous dealer by default.

---

## 3. Cards: hierarchy and point values

The same physical card can be worth different numbers of points depending on whether it is currently trump or not, and the ranking within a suit also changes. This is the single trickiest rule for newcomers.

### 3.1. Trump suit (strongest first)

| Card       | Jack | 9   | Ace | 10  | King | Queen | 8   | 7   |
| ---------- | ---- | --- | --- | --- | ---- | ----- | --- | --- |
| **Points** | 20   | 14  | 11  | 10  | 4    | 3     | 0   | 0   |

The Jack and the 9 are the master cards at trump. Mnemonic:
**V 9 A 10 R D 8 7**.

### 3.2. Non-trump suits (strongest first)

| Card       | Ace | 10  | King | Queen | Jack | 9   | 8   | 7   |
| ---------- | --- | --- | ---- | ----- | ---- | --- | --- | --- |
| **Points** | 11  | 10  | 4    | 3     | 2    | 0   | 0   | 0   |

Standard order outside trump: **A 10 R D V 9 8 7**.

### 3.3. All-trump contracts (strongest first, every suit)

Under an **all-trump** contract (§5.2) every suit ranks like trump, with a dedicated scale so the deck total stays constant:

| Card       | Jack | 9   | Ace | 10  | King | Queen | 8   | 7   |
| ---------- | ---- | --- | --- | --- | ---- | ----- | --- | --- |
| **Points** | 14   | 9   | 6   | 5   | 3    | 1     | 0   | 0   |

Order within every suit: **V 9 A 10 R D 8 7** — 38 points per suit, 152 in the deck, exactly as in a suit contract.

### 3.4. No-trump contracts (strongest first, every suit)

Under a **no-trump** contract every suit ranks like a plain suit, rescaled to
keep the deck total constant:

| Card       | Ace | 10  | King | Queen | Jack | 9   | 8   | 7   |
| ---------- | --- | --- | ---- | ----- | ---- | --- | --- | --- |
| **Points** | 19  | 10  | 4    | 3     | 2    | 0   | 0   | 0   |

Order within every suit: **A 10 R D V 9 8 7**.

### 3.5. Totals

- 152 points live in the cards themselves, under **every** contract mode (suit, all-trump, no-trump).
- An extra 10 points (the **last trick bonus**) go to whichever team wins the last trick.
- Per round, **162 points** are distributed across the two teams.
- Belote (see §6.6) adds 20 points per belote where it exists: exactly one is possible in a suit contract (per-round ceiling 182); none at no-trump; at all-trump the agreed belote regime (§6.6) allows zero, one, or up to four (ceiling 242).

There is no hierarchy between the three non-trump suits — they are all equal,
beaten only by the trump suit.

---

## 4. Phase 1 — Deal

1. For the very first round of a game, the dealer is chosen at random.
2. For subsequent rounds:
   - The dealer is the next player along in the turn direction (to the right of the previous dealer by default).
   - The deck is **not** reshuffled between rounds by default. The collected
     pile is simply *cut* by the player on the dealer's other side (to the dealer's left when play runs anticlockwise) and then dealt. The *cut* must leave at least three cards on either side of it — a sliver off the top or the bottom would leave the rest of the pile in its known order, so the three-card margin is an anti-cheating convention.
   - **Table option — reshuffle every round** (off by default, §9): the deck is shuffled before every deal instead of merely cut.
3. Dealing pattern: groups of **3-2-3** cards to each player, in the turn order (anticlockwise by default). Other patterns — 4-4 (the *Corsica* deal), 2-3-3, 3-3-2 — are acceptable as long as everyone agrees in advance; they are catalogued in §9 but are not part of the canonical rule set.
4. After dealing, each player has 8 cards. No card is turned up; all 32 are
   distributed.

> **Table options (§9), both off by default:**
> 
> - **Six-card auction deal** (*"à la stéphanoise"*)— only **6** of the 8 cards are dealt before the auction, the remaining two per player following once the contract is settled. Bidding on partial information changes the auction completely, and this is the only setting under which 5-point bid increments can be available (§5.2).
> - **Same dealer redeals** — when the auction ends with everyone passing, the *same* dealer deals the next round instead of passing the deal along (§5.4).

---

## 5. Phase 2 — Bidding

This is the strategic core of the game and what separates Contrée from classical Belote.

### 5.1. Order

The first player to speak is the one after the dealer in the turn direction — to the **right of the dealer** when play runs anticlockwise — and the auction continues that way around the table.

> **Table option — forced dealer bid** (off by default, §9): if the three players before the dealer all pass, the dealer *must* bid rather than pass, so the round can never be annulled for want of a contract (§5.4).

### 5.2. Possible actions on your turn

- **Make a bid.** Announce a *value* and a *trump choice* — one of the four
  suits (e.g. `90 ♥`), **no trump**, or **all trump**. The value is the number
  of points your team commits to taking under that trump regime.
  
  - Minimum opening bid: **80**.
  
  - Increments: **10 points**.
    
    > *Table option — 5-point increments* (off by default, §9): steps of 5 instead, available only alongside the six-card auction deal (§4).
  
  - Each new bid must be strictly higher than the current one, whatever its
    trump choice.
  
  - **Table option — extended trump choices** (off by default, §9): *no trump* and *all trump* are legal trump choices only where the table has enabled them; otherwise the four suits are the only options. Enabling them also brings in the per-mode card scales (§3.3, §3.4), the play rules of §6.4 and the all-trump belote regime (§6.6).
  
  - Maximum numeric bid — the ladder top depends on the trump choice and, at all trump, on the agreed belote regime (§6.6):
  
  | Trump choice                        | Ladder top | Highest take possible |
  | ----------------------------------- | ---------- | --------------------- |
  | Suit (♠ ♥ ♦ ♣)                      | 180        | 162 + 20 Belote = 182 |
  | No trump                            | 160        | 162 (no Belote)       |
  | All trump — no-belote regime        | 160        | 162                   |
  | All trump — single belote (default) | 180        | 162 + 20 = 182        |
  | All trump — four belotes            | 240        | 162 + 80 = 242        |
  
  > The ladder top is simply the last 10-point step at or below that ceiling, which is why the two columns never match exactly. Cards plus the last-trick bonus cap at 152 + 10 = **162**, so a mode with no Belote available stops the ladder at 160; each Belote the regime allows lifts the ceiling by 20 (182, then 242) and the ladder along with it (180, then 240). The steps above 160 are therefore only feasible with the required **Belote** in hand. The auction does **not** enforce that at bid time: bidding one of them without the Belote is legal but commits the bidder to a contract they cannot make on cards alone, which will *fail* at scoring.

- **Bid Slam**. A special bid declaring your team will take **all 8 tricks**. Contract base value **250** points. Slam outranks any numeric bid: once bid, no further contract bid is legal (numeric, Slam, or Solo Slam). *Double* and *redouble* are available against a Slam by default but this can be turned off (see §5.3).

- **Bid Solo Slam**. A stronger all-tricks bid declaring that the **bidder personally** will win every one of the 8 tricks — their partner may play normally but is forbidden from winning any trick. Contract base value **500** points. Solo Slam outranks any numeric bid, but it **cannot be bid after a Slam** — once a Slam is on the table, the auction is closed to further contract bids. A Solo Slam closes the ladder in exactly the same way: once it is bid, no further contract bid is legal either. What is *asymmetric* is only the Slam → Solo Slam direction — Slam blocks the higher-ranked Solo Slam, where plain precedence would have let it through. *Double* and *redouble* are available against a Solo Slam by default but this can be turned off (see §5.3).

> Slam-family bids exist under every trump choice (suit, no trump, all trump), with the same base values. Some tables disallow the **Solo Slam** bid altogether — its availability is agreed before the game (allowed by default, §9).

- **Pass**. A player who passes may re-enter the bidding later, as
  long as the auction has not yet ended.

- **Double** — see §5.3.

- **Redouble** — see §5.3.

> **Table option — Nullo contract** (off by default, §9): an extra bid undertaking that the declaring team will take **fewer than 11 points** over the 8 tricks — an undertaking to lose rather than to win. Not part of the canonical rule set.

### 5.3. Doubling

Doubling offers an opportunity to increase to points at stake during a round. This can be done when a team estimates that the contract cannot be done by the attacking team.

- **Double**: an opponent of the current bidder may call *double* instead of passing or bidding. This **freezes** the auction at the current contract and **doubles** the point value at stake (both for success and for failure).
- **Redouble**: the bidder's team may respond to a double with a *redouble*, which **quadruples** the point value at stake. Either player on the bidding team may do this.
- *Double* can only be called on the most recent contract bid — numeric or Slam-family alike, subject to the two switches below (it cannot be used to reopen a finished auction).
- **Intervening passes do not close the Double/ Redouble window.** Both *double* (by an opposing player) and *redouble* (by the bidding team) remain legal up until the auction terminates on three consecutive passes per §5.4 — players who passed earlier may re-enter and call *double* or *redouble*, consistent with the general re-entry rule in §5.2.
- **Table options (§9):** *Slam can be doubled* and *Solo Slam can be doubled* are two independent switches, both **on** by default. Turning one off forbids *double* — and therefore *redouble* — against that contract entirely; the other contract is unaffected.

### 5.4. End of bidding

The auction ends when three consecutive players pass after the last bid —
always there, and only there. A *double* or a *redouble* freezes the contract
but does not close the auction: the reply window of §5.3 stays open until those
three passes land.

- The team holding the final bid becomes the **declarer** / *attacker* .
- The other team is the **defense**.
- The suit of the final bid is the **trump** for this round.
- If everyone passes without anyone bidding, the round is annulled: cards are
  collected and the **next dealer** (normal rotation, §2) redeals — unless the *same dealer redeals* option is on (§4, §9), or the *forced dealer bid* option makes an all-pass auction impossible in the first place (§5.1).

---

## 6. Phase 3 — Card play

The first card of the round (the *lead*) is played by the player after the dealer in the turn direction — to the **right of the dealer** when play runs anticlockwise — regardless of which team won the contract.

> **Table option — the Solo Slam gives the lead** (off by default, §9): the declarer of a Solo Slam contract leads the first trick instead. Nothing else changes — play resumes in the normal turn order from that lead, trick winners still lead the following tricks, and the next round's dealer is still the seat after the previous dealer, never the Solo Slam declarer.

### 6.1. The trick

Each trick has 4 cards, one per player, played in the set order, player by player. The winner of a trick leads the next one. There are 8 tricks per round.

### 6.2. Card-play obligations (in order)

The legal-move rules of contrée are strict. Given the suit led (the first card played), a player must obey the following, in order:

1. **Follow suit.** If you have any card in the led suit, you must play one.
2. **Trump if you cannot follow.** If you have no card in the led suit, you must play a trump, *unless* exception applies (bullet 4 or under-trump exemption option).
3. **Overtrump if a trump has been played to this trick.** If trumps have already been played and you must trump, you must play a trump *higher* than the highest trump already on the table, if you have one. If you hold no trump able to beat it, you must still play a trump — an *under-trump* — and you may choose which one, unless the under-trump exemption below is active.
4. **Partner exception.** If your partner is currently winning the trick (their card is the strongest played so far), you are *not* obligated to trump or to overtrump if you can't provide a card from the led suit. You may discard freely.Here, discard means playing any card, including trumps even if it's not mandatory to play one. Often discarding is used to play cards that owns a lot of points or to create trump opportunities by discard a card from a singleton.
5. **Discard.** If you have neither the led suit nor a trump (and no obligation forces a trump), you may play any card.

> **Table option — under-trump exemption** (on by default, §9): when you are void in the led suit, an **opponent** has already trumped, and you hold no trump able to beat the best trump on the table, you may discard freely instead of playing a losing trump. The obligations to over-trump when you can, to trump when nobody has cut yet, and the trump-led rule (§6.3) are unchanged.

### 6.3. Special case: trump is led

When trump is led, the follow-suit rule (1) applies as usual. In addition, every player who can must play a trump *higher* than the highest already on the table, if they hold one. If they cannot beat it, they must still play a trump, but they may choose which one.

### 6.4. No-trump and all-trump play

- **No trump:** there is no trump suit. Follow the led suit if you can; otherwise discard freely, no trumping, no over-trumping, no partner exception to worry about. The highest card of the led suit (§3.4 order) wins the trick.
- **All trump:** every suit is trump, and every trick behaves like §6.3 in the led suit: follow it *and* play a card **higher** than the best one already on the table if you can; if you cannot beat it, you must still follow. When you are void in the led suit you discard freely — cards of the other suits can never win the trick, so there is no cross-suit cutting. The highest card of the led suit (§3.3 order) wins.

Both modes leave the **under-trump exemption** (§6.2, §9.5) inert, for opposite reasons: at no trump there is no trump to be forced into playing, and at all trump a player void in the led suit already discards freely, so no losing trump was ever owed. The switch changes nothing in either — it is a suit-contract rule.

### 6.5. Winning a trick

- If the trick contains any trumps, the highest trump wins.
- Otherwise, the highest card *in the led suit* wins. Cards of other non-trump
  suits cannot win.

These two rules cover suit contracts; §6.4 states the winner at no trump and all trump (always the highest card of the led suit).

### 6.6. Belote / Rebelote

If a player holds **both** the King and the Queen of trump, they may declare this for a 20-point bonus to their team. The declaration is verbal:

- Say "**Belote**" when playing the first of the two cards.
- Say "**Rebelote**" when playing the second.

Notes:

- The bonus is awarded to the team regardless of which of the two cards is played first.
  
  > **Table option — King announced first** (off by default, §9): the pair must be opened with the **King**, "Belote" on the King and "Rebelote" on the Queen, and a holder who plays the Queen first forfeits the bonus.

- **Where Belote exists.**
  
  - Suit contracts: only K + Q of the trump suit, exactly one belote possible.
  - No trump: no belote at all.
  - All trump: the table agrees a *belote regime* before the game (§9) between **none**, **single** (the default) and **four**.
    - Under **single**, only one belote counts for the whole round: the **first one announced in play**, meaning the first time any holder plays either the King or the Queen of a pair and announces it. A second holder announcing later scores nothing, whichever team they are on.
    - Under **four**, every K + Q pair held counts 20 each, so one team may score up to +80 — which is what lifts the all-trump ladder to 240 (§5.2).

- **It counts toward the contract** by default (§9) — both for reaching the contract
  value and for out-scoring the defense where that rule applies (§7.5). Tables may agree instead that Belote never enters those tests and is only added to the marked score afterwards. The similar rule exists for the end of game (§8).

- It is **kept even if the contract fails** by default. This is non-obvious and worth testing carefully in the engine. A table option (§9) transfers the failing attackers' Belote (20 points each) to the defense instead; a defending team's Belote is never taken.

---

## 7. Phase 4 — Scoring

### 7.1. Counting

At the end of the 8 tricks:

1. Each team sums the point values of the cards in the tricks it has won (using the *current* trump values, see §3).
2. The team that won the last trick adds the **last trick bonus** (10 points).
3. Belote bonus (20) is added if applicable.

The total across both teams (excluding Belote) is always **162**.

### 7.2. Contract outcome

Let:

- `C` = contract value — a numeric bid (80, 90, … up to the ladder top of the trump choice, §5.2) or the base value of a Slam-family bid (250 / 500)
- `P_attack` / `P_defense` = points realized by each team (cards + last-trick bonus + Belote where it counts)
- `M` = multiplier: 1 (no double), 2 (double), 4 (redouble)

#### The two components of a mark

Every mark written at the end of a round is the sum of **two components**:

| Component            | Represents                        | Marking convention (§7.3) |
| -------------------- | --------------------------------- | ------------------------- |
| **Made points**      | what the trick pile is worth      | *made points*             |
| **Announced points** | what the contract itself is worth | *announced points*        |

A table marks one or both — at least one is mandatory (§7.3). The rules below define each component separately; a side's mark is whatever its table's active conventions add up to. Every grid in this section assumes the default, where **both** are marked.

#### Numeric contracts, un-doubled (`M = 1`)

The two sides *share* the pile:

| Outcome                     | Made-points component                              | Announced-points component |
| --------------------------- | -------------------------------------------------- | -------------------------- |
| **Made** (`P_attack ≥ C`)   | declarer `P_attack`, defense `P_defense`           | declarer `C`, defense 0    |
| **Failed** (`P_attack < C`) | defense **160** (the whole pile, flat), declarer 0 | defense `C`, declarer 0    |

- Made, worked example: contract `90 ♥`, declarer realizes 102 → declarer 192, defense 60.
- Failed, worked example: contract `100 ♠`, failed → defense 260, declarer 0.

> On a failure the defense takes the pile whole, so its 162 points are written as a flat **160** instead of being counted out. The same flattening applies to every doubled round.

#### Numeric contracts, doubled and redoubled (`M > 1`)

A double turns the round into a single stake, **winner takes all**. The winning side — declarer if the contract is made, defense if it failed — marks the whole amount; the **losing side marks 0**, whatever tricks it took.

**Table option — only announced points (`A`) are multiplied** (on by default, §9) decides where the multiplier bites:

| *Only announced points are multiplied* | Made-points component | Announced-points component | Total           |
| -------------------------------------- | --------------------- | -------------------------- | --------------- |
| **on** (default)                       | flat **160**          | `A × M`                    | `160 + A × M`   |
| off                                    | `160 × M`             | `A × M`                    | `(160 + A) × M` |

`A` is the announced-points component. It is the contract value `C` — on a made contract always, and on a failed one too. Exactly one switch ever changes it: *any failure marks 160* (off by default, see the end of this section), which replaces `C` with a flat **160**, and only on failures.

- Worked example, default: contract `100 ♥ ×2` made → declarer 360, defense 0; the same contract failed → defense 360, declarer 0.
- The second row for the same contract made → declarer 520, defense 0; the same contract failed → defense 520, declarer 0.

**Belote (+20)** is the standing exception to "the loser marks 0": it is always credited to the team **holding** K + Q of trump, except under the belote-loss option (end of §6.6).

#### Unannounced Slam

If the declaring team wins **all 8 tricks** on a numeric contract *without having bid a Slam*, its made-points component is not the pile but a flat **250** substitute, which absorbs the 152 card points and the 10-point last-trick bonus alike: the declarer marks `A + 250`, the defense marks nothing, and the contract is necessarily **made** (sweeping every trick cannot fail any contract). This mirrors the announced-Slam shape while keeping the numeric contract value `A` as the announced component.

- If the two partners split the 8 tricks between them, the substitute is **250**. If the **declarer** personally took all 8, it is **500** (where the Solo Slam bid is authorized, §9.4) — the sweep the declarer could have bid as a Solo Slam. A *partner's* solo sweep is not that shape and scores the ordinary **250** team substitute.
- **Declaring team only.** If the *defense* takes all 8 tricks the declarer has simply failed — mark it as an ordinary failed contract, not as a Slam.
- **Un-doubled only.** A doubled or redoubled sweep uses the winner-takes-all grid above, pile flat at 160; the substitute does **not** apply.
- **Table option — unannounced-Slam substitute** (on by default, §9): switched off, a sweep marks the ordinary pile like any other made contract.
- **Belote (+20)** still layers on top for the holding team, as everywhere.

> Worked example: contract `100 ♠`, declaring team sweeps all 8 → declarer
> 350 (`100 + 250`), defense 0; if the **declarer** took all 8 alone → 600
> (`100 + 500`).

#### Slam and Solo Slam

An announced Slam-family contract replaces the trick pile with a flat **substitute** equal to its own base value. Its two components are therefore the substitute and `C`, which happen to be equal:

| Bid       | `C` | Substitute (replaces the pile) |
| --------- | --- | ------------------------------ |
| Slam      | 250 | 250                            |
| Solo Slam | 500 | 500                            |

Feeding those into the component rules above — the substitute standing in for the flat 160, `C` for the contract and the option — gives:

| Contract  | Normal | Doubled | Redoubled |
| --------- | ------ | ------- | --------- |
| Slam      | 500    | 750     | 1250      |
| Solo Slam | 1000   | 1500    | 2500      |

and, with *only announced points are multiplied* switched off, both components take the multiplier instead:

| Contract  | Normal | Doubled | Redoubled |
| --------- | ------ | ------- | --------- |
| Slam      | 500    | 1000    | 2000      |
| Solo Slam | 1000   | 2000    | 4000      |

The grid is **symmetric**: whichever side wins the contract marks the amount (declarer if made, defense if failed). The other side marks zero (modulo Belote — see below).

**Slam** is **made** when the declaring team wins **all 8 tricks**. Anything less is a failure → the defense marks the amount.

**Solo Slam** is **made** only when the **declaring player personally** wins every one of the 8 tricks. The team winning all 8 together is **not** enough — if the partner wins any trick, the Solo Slam fails and the defense marks the amount.

**Belote (+20)** still applies on top of the Slam grid: it goes to whichever team holds the K + Q of trump, independent of which side wins the contract.

**Last trick bonus** does **not** apply on a Slam-family round — the substitute already covers the full trick pile.

#### Failure marks — table options

Three switches reshape what a *failed* contract marks (all catalogued in §9):

- **Any failure marks 160** (off by default). Switched on, the announced-points component `A` of a failed contract becomes a flat **160** instead of `C`, so every un-doubled failure marks 320 whatever the contract was worth.
- **Failed Slam marks 250 / 500 — made points** (on by default). Switched off, a failed Slam-family contract falls back to the ordinary flat-160 pile.
- **Failed Slam marks 250 / 500 — announced points** (on by default). Only meaningful when *any failure marks 160* is on, and inert otherwise: under the default the announced component of a failed Slam is already `C`, which is 250 or 500. If this is off the announced part of a failed Slam contract is 160.

### 7.3. Marking conventions

Two independent switches decide which of the §7.2 components are actually written down (§9). **At least one must be active** — a table marking neither would keep no score at all.

| Convention           | Default | Marks                                  |
| -------------------- | ------- | -------------------------------------- |
| **Made points**      | on      | the made-points component of §7.2      |
| **Announced points** | on      | the announced-points component of §7.2 |

For an un-doubled round, the three legal combinations mark:

| Active conventions      | Made contract                               | Failed contract                                         |
| ----------------------- | ------------------------------------------- | ------------------------------------------------------- |
| Both (default)          | declarer `C + P_attack`; defense its points | defense `160 + C`                                       |
| *Made points* only      | declarer `P_attack`; defense its points     | defense **160** (Slam-family: the 250 / 500 substitute) |
| *Announced points* only | declarer `C`; defense **0**                 | defense `C` (Slam-family: 250 / 500)                    |

Doubled and redoubled rounds keep the winner-takes-all shape of §7.2 with the inactive component dropped, subject to one override:

- **When *announced points* are not marked, the multiplier falls on the made-points component** — otherwise a double would change nothing at all. Such a round marks `160 × M` (Slam-family: `250 × M` / `500 × M`), whatever the *only announced points are multiplied* switch says.    
- When *made points* are not marked, the announced component carries the round alone: `A × M` by default, or `160 × M` (Slam-family: `250 × M` / `500 × M`) with *only announced points are multiplied* switched off.

Belote follows its own rules (§6.6) on top of every convention.

### 7.4. Rounding

**Table option — rounding** (§9), with three values:

- **Exact** (default) — marks are written as they come.
- **Nearest 10** — half rounds **up**: 85 → 90. A raw 85–77 split therefore marks
  90–80 — exceptionally 170 in total.
- **Nearest 5** — with integer piles, ties are impossible.

Rounding affects the *marks* only; whether the contract is made is always judged
on exact points. The flat components — 160, the 250 / 500 substitutes, the
contract value — are already round, so in practice only a shared pile moves.

### 7.5. Out-scoring the defense & dispute

**Table option — the attack must out-score the defense** (on by default, §9): to make its contract the attack must not only reach `C` but also score **strictly more than the defense** (`P_attack > P_defense`), Belote included on both sides whenever it counts toward the contract (§6.6).

A **dispute** is an exact tie. On cards alone that is 81 / 81 (the pile being 162). When **one** side's Belote counts it is 91 / 91 — 71 + 20 against 91, out of 182. And where the all-trump *four*-belote regime (§6.6) puts a Belote on each side, 101 / 101 — 81 + 20 each, out of 202. The option settles the tie on its own, so there is nothing further to agree:

- **On** (default): the attack has not out-scored the defense, so the contract **fails** and ordinary failed-contract marking applies.
- **Off**: only `P_attack ≥ C` matters, so a tie leaves the contract made whenever the attack reached its value, and each team marks its own points as usual.

### 7.6. Double/ Redouble multiplier

The multiplier `M` from §7.2 applies whether the contract is made or failed. Doubling cuts both ways — it punishes overbidding *and* rewards a successful defense.

---

## 8. End of game

- Each game is played to a **target score** agreed beforehand: 500, 1000, 1500, **2000** (default), 3000, 4000 or 5000 (§9).
- The first team to reach or exceed the target at the end of a round wins the game.
- If both teams cross the target in the same round, the higher score wins.
- If both teams sit at the **same score** at or above the target, nobody has won yet: play continues with one additional round (sudden death).
- **Table option — win on Belote points alone** (on by default, §9): a team may cross the target on Belote points like on any others. Switched off, a team taken past the target by its Belote bonus alone has not won yet — the win waits until points from play confirm it. This applies to attacking and defending teams alike.
- Each game starts again from zero, and the **match** goes to the first team to win the agreed number of games (**1** by default, §9).

---

## 9. Variants & options — catalogue

The master list. Every switch named anywhere in this document appears here with its default, and nothing that is absent here is a switch. **Scope** records how far the ContrAI software takes it:

- **configurable** — a knob the software is meant to expose.
- **documented only** — a genuine table variant, recorded for completeness and deliberately left unimplemented.
- **interface aid** — not a rule of the game at all, a convenience of the interface.

Defaults are in **bold**.

The *documented only* rows are tracked together as a deferred-variants checklist in [issue #12](https://github.com/valmathieu/ContrAI/issues/12), one entry each; any of them can be split off and implemented on its own whenever it becomes interesting.

### 9.1. General

| Option                 | Values                                            | Scope           | Where  |
| ---------------------- | ------------------------------------------------- | --------------- | ------ |
| Winning games required | **1** / any `N`                                   | documented only | §1, §8 |
| Target score per game  | 500 / 1000 / 1500 / **2000** / 3000 / 4000 / 5000 | configurable    | §1, §8 |
| Turn direction         | **anticlockwise** / clockwise                     | configurable    | §2     |

### 9.2. Trump variants

| Option                                        | Values                   | Scope        | Where |
| --------------------------------------------- | ------------------------ | ------------ | ----- |
| Extended trump choices (no trump + all trump) | **off** / on             | configurable | §5.2  |
| All-trump belote regime                       | none / **single** / four | configurable | §6.6  |

### 9.3. Deal

| Option                                | Values                                    | Scope           | Where    |
| ------------------------------------- | ----------------------------------------- | --------------- | -------- |
| Deal pattern                          | **3-2-3** / 4-4 (Corsica) / 2-3-3 / 3-3-2 | documented only | §4       |
| Reshuffle every round                 | **off** / on                              | configurable    | §4       |
| Six-card auction deal                 | **off** / on                              | documented only | §4       |
| Same dealer redeals after an all-pass | **off** / on                              | documented only | §4, §5.4 |

### 9.4. Bidding

| Option                                         | Values       | Scope           | Where |
| ---------------------------------------------- | ------------ | --------------- | ----- |
| 5-point bid increments (six-card auction only) | **off** / on | documented only | §5.2  |
| Forced dealer bid                              | **off** / on | documented only | §5.1  |
| Solo Slam available                            | **on** / off | configurable    | §5.2  |
| Nullo contract available                       | **off** / on | documented only | §5.2  |
| Slam can be doubled                            | **on** / off | configurable    | §5.3  |
| Solo Slam can be doubled                       | **on** / off | configurable    | §5.3  |

### 9.5. Card play

| Option                              | Values       | Scope           | Where |
| ----------------------------------- | ------------ | --------------- | ----- |
| Under-trump exemption               | **on** / off | configurable    | §6.2  |
| The Solo Slam gives the lead        | **off** / on | configurable    | §6    |
| Belote counts toward the contract   | **on** / off | configurable    | §6.6  |
| Belote lost when the contract fails | **off** / on | configurable    | §6.6  |
| King announced first for Belote     | **off** / on | documented only | §6.6  |

### 9.6. Scoring

| Option                                         | Values                             | Scope        | Where |
| ---------------------------------------------- | ---------------------------------- | ------------ | ----- |
| Made-points marking                            | **on** / off                       | configurable | §7.3  |
| Announced-points marking                       | **on** / off                       | configurable | §7.3  |
| Only announced points are multiplied           | **on** / off                       | configurable | §7.2  |
| Any failure marks 160                          | **off** / on                       | configurable | §7.2  |
| Unannounced-Slam substitute (250 / 500)        | **on** / off                       | configurable | §7.2  |
| Failed Slam marks 250 / 500 — made points      | **on** / off                       | configurable | §7.2  |
| Failed Slam marks 250 / 500 — announced points | **on** / off                       | configurable | §7.2  |
| The attack must out-score the defense          | **on** / off                       | configurable | §7.5  |
| Rounding                                       | **exact** / nearest 10 / nearest 5 | configurable | §7.4  |
| Win on Belote points alone                     | **on** / off                       | configurable | §8    |

At least one of the two marking conventions must be on, and *failed Slam marks 250 / 500 — announced points* has no effect unless *any failure marks 160* is on. Every other combination in this table is free.

### 9.7. Table aids

| Option           | Values       | Scope         | Where |
| ---------------- | ------------ | ------------- | ----- |
| Live round score | **on** / off | interface aid | —     |

*Live round score* shows each side's running card points during the play phase. Around a real table the count is a memory exercise, so this is an assistance setting rather than a rule.

### 9.8. Out of scope

- **Melds**: extra bonuses declared at the start of the first trick for card combinations held in hand — sequences of three, four or five cards, and four of a kind. Inherited from classical Belote. **Out of scope for ContrAI** — playing without them is exactly what distinguishes contrée from coinche.

---

## 10. Terminology — FR ↔ EN

For the bilingual report and for keeping Claude consistent across languages.

| French                  | English                       | Notes                                                                       |
| ----------------------- | ----------------------------- | --------------------------------------------------------------------------- |
| À la stéphanoise        | Six-card auction deal         | Only 6 of the 8 cards dealt before the auction (§4)                         |
| À la vache              | Forced dealer bid             | The dealer may not pass if the three players before them did (§5.1)         |
| Atout                   | Trump                         |                                                                             |
| Annonce                 | Bid                           | The verb is *to bid*; *announce* survives in Belote and the §7.2 terms      |
| Annonces (combinaisons) | Melds                         | Belote-style bonuses for combinations held in hand — out of scope (§9.8)    |
| Belote                  | Belote                        | The K+Q-of-trump bonus                                                      |
| Capot                   | Slam                          | Taking all 8 tricks (the *team* wins them all)                              |
| Capot général           | Solo Slam                     | Bidder *personally* takes all 8 tricks (cannot follow a Slam)               |
| Capot non annoncé       | Unannounced Slam              | All 8 tricks swept without having bid a Slam (§7.2)                         |
| Chute / Chuter          | Failure / to fail             | Used when the declarer does not make the contract                           |
| Contrat                 | Contract                      | The bid value                                                               |
| Contre / Contrer        | Double / to double            |                                                                             |
| Coupe / Couper          | Trump (n.) / to trump (v.)    | *Couper* = play a trump on a non-trump-led trick                            |
| Défausse / Se défausser | Discard / to discard          |                                                                             |
| Défense                 | Defense                       | The non-declaring team                                                      |
| Der / Dix de der        | Last trick / last-trick bonus | 10 points                                                                   |
| Dispense de pisser      | Under-trump exemption         | Table option (§6.2)                                                         |
| Donne                   | Round                         | One complete deal + bidding + 8 tricks + scoring                            |
| Donneur                 | Dealer                        |                                                                             |
| Enchères                | Auction                       |                                                                             |
| Entame / Entamer        | Lead / to lead                | First card of a trick                                                       |
| Fournir                 | To follow suit                |                                                                             |
| Générale                | Solo Slam                     | Regional synonym of *capot général*                                         |
| Levée                   | Trick                         | Synonym of *pli*                                                            |
| Litige                  | Dispute                       | An exact tie between the two sides (§7.5)                                   |
| Main                    | Hand                          | The 8 cards a player holds                                                  |
| Manche                  | Game                          | A sequence of rounds played to the target score (§8) — *not* a single round |
| Maître / Maîtresse      | Master                        | A card guaranteed to win (in its suit, given what has fallen)               |
| Misère                  | Nullo contract                | Documented-only bid: take fewer than 11 points (§5.2)                       |
| Monter                  | To raise / to overtrump       | *Monter à l'atout* = play a higher trump                                    |
| Partie                  | Match                         | A sequence of games; a single game by default (§1, §8)                      |
| Passer                  | To pass                       |                                                                             |
| Pisser                  | To under-trump                | Play a losing trump by obligation                                           |
| Pli                     | Trick                         | Synonym of *levée*                                                          |
| Points annoncés         | Announced points              | Marking component: what the contract itself is worth (§7.2, §7.3)           |
| Points faits            | Made points                   | Marking component: what the trick pile is worth (§7.2, §7.3)                |
| Preneur / Prenante      | Declarer / declaring team     | The team that won the contract                                              |
| Rebelote                | Rebelote                      | Second of the Belote pair                                                   |
| Sans atout              | No trump                      | Variant (§3.4, §5.2)                                                        |
| Sens de jeu             | Turn direction                | Anticlockwise by default (§2)                                               |
| Singlette               | Singleton                     | Have only one card in a suit                                                |
| Surcontre / Surcontrer  | Redouble / to redouble        |                                                                             |
| Surcouper               | To overtrump                  |                                                                             |
| Tout atout              | All trump                     | Variant (§3.3, §5.2)                                                        |
| Valet                   | Jack                          | Top trump card                                                              |

---

## 11. Quick reference — round flow

```text
[Deal]      → 8 cards each, 3-2-3 in the turn direction (anticlockwise)
   ↓
[Bidding]   → starting after the dealer
              actions: bid (80 up to the mode ladder — suit / no trump /
              all trump), slam, solo slam, double, redouble, pass
              ends: 3 consecutive passes after the last bid
   ↓
[Card play] → 8 tricks, lead = the player after the dealer
              obey: follow → trump → overtrump (except partner-master) → discard
              optional: announce Belote/Rebelote on K/Q of trump
   ↓
[Scoring]   → made-points component + announced-points component
              apply contract success/failure + multiplier
   ↓
[Check]     → if one team strictly leads at ≥ target (2000 by default): game won
              tie at ≥ target: sudden death, keep playing until one team leads
              else next round, dealer rotates in the turn direction
   ↓
[Match]     → first team to win N games (N = 1 by default) takes the match
```
