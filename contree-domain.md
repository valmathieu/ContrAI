# Contrée — Domain Knowledge

> **Scope.** This document captures the *game* of Contrée independent of any
> software implementation. It is the canonical reference for rules,
> terminology, and community conventions used across the ContrAI project. For
> *what the engine does*, see `Specs_logicielles.md` and `Specs_fonctionelles.md`;
> for *how the AI reasons*, see the AI-family docs alongside their
> implementations.

---

## 1. Overview

Contrée is a French trick-taking card game for four players in two fixed
partnerships. It is a member of the Jass family (Klaverjassen → Belote →
Contrée) and inherits its bidding mechanism from Bridge: each round, the two
teams bid against each other for the right to choose the trump suit and to
commit to a points contract.

A game consists of multiple rounds (**manches**); each round runs through four
phases:

1. **Deal** (*distribution*)
2. **Bidding** (*enchères*)
3. **Card play** (*le jeu de la carte*) — 8 tricks
4. **Scoring** (*comptage*)

The first team to reach a target score (commonly 1500 or 2000 points) wins.

---

## 2. Setup

- **Players.** Exactly 4.
- **Teams.** Two fixed pairs, partners seated opposite each other. By
  convention we call them **North–South** and **East–West** (N–S vs E–W), as
  in Bridge.
- **Deck.** 32 cards: 7, 8, 9, 10, Jack, Queen, King, Ace in each of four suits
  (♠ ♥ ♦ ♣).
- **Turn order.** Anticlockwise. The player to the **right** of the current
  actor plays next.
- **Dealer rotation.** Anticlockwise — each new round, the dealer is the player
  to the right of the previous dealer.

---

## 3. Cards: hierarchy and point values

The same physical card can be worth different numbers of points depending on
whether it is currently trump or not, and the ranking within a suit also
changes. This is the single trickiest rule for newcomers, and it is the source
of most edge cases in the engine.

### 3.1. Trump suit (strongest first)

| Card       | Jack | 9   | Ace | 10  | King | Queen | 8   | 7   |
| ---------- | ---- | --- | --- | --- | ---- | ----- | --- | --- |
| **Points** | 20   | 14  | 11  | 10  | 4    | 3     | 0   | 0   |

The Jack (*Valet*) and the 9 are the master cards at trump. Mnemonic:
**V 9 A 10 R D 8 7**.

### 3.2. Non-trump suits (strongest first)

| Card       | Ace | 10  | King | Queen | Jack | 9   | 8   | 7   |
| ---------- | --- | --- | ---- | ----- | ---- | --- | --- | --- |
| **Points** | 11  | 10  | 4    | 3     | 2    | 0   | 0   | 0   |

Standard order outside trump: **A 10 R D V 9 8 7**.

### 3.3. Totals

- 152 points live in the cards themselves.
- An extra 10 points (the **dix de der**) go to whichever team wins the last
  trick.
- Per round, **162 points** are distributed across the two teams.
- The Belote bonus (see §6.5) adds 20 points to one team's total if applicable,
  bringing the per-round ceiling to 182.

There is no hierarchy *between* the three non-trump suits — they are all equal,
beaten only by the trump suit.

---

## 4. Phase 1 — Deal

1. For the very first round of a game, the dealer is chosen at random.
2. For subsequent rounds:
   - The dealer is the player to the right of the previous dealer.
   - The deck is **not** reshuffled between rounds by default. The collected
     pile is simply *cut* by the player to the dealer's left and then dealt.
     (Players may agree before the game to reshuffle every time.)
3. Dealing pattern: groups of **3-2-3** cards to each player, anticlockwise.
   Variants like 2-3-3 or 3-3-2 are also acceptable as long as everyone agrees
   in advance.
4. After dealing, each player has 8 cards. No card is turned up; all 32 are
   distributed.

---

## 5. Phase 2 — Bidding

This is the strategic core of the game and what separates Contrée from
classical Belote.

### 5.1. Order

The first player to speak is the one to the **right of the dealer**. Bidding
proceeds anticlockwise.

### 5.2. Possible actions on your turn

- **Make a bid.** Announce a *value* and a *suit*, e.g. `90 ♥`. The value is
  the number of points your team commits to taking with that suit as trump.
  - Minimum opening bid: **80**.
  - Increments: **10 points**.
  - Maximum numeric bid: **180**.
  - Each new bid must be strictly higher than the current one.

  > The 170 and 180 steps are only feasible with **Belote** in hand (K + Q of
  > trump add 20 points), since the cards alone cap at 162 + 10 *dix de der* =
  > 172. The auction does **not** enforce that constraint at bid time —
  > announcing 170 / 180 without Belote is legal but commits the bidder to a
  > contract they cannot make on cards alone, which will *chuter* at scoring.
- **Bid Slam** (*Capot*). A special bid declaring your team will take **all 8
  tricks**. Contract base value **250** points. Slam outranks any numeric
  bid: once declared, no further contract bid is legal (numeric, Slam, or
  Solo Slam). *Contre* and *surcontre* remain available against a Slam.
- **Bid Solo Slam** (*Capot général*). A stronger all-tricks bid declaring
  that the **bidder personally** will win every one of the 8 tricks — their
  partner may play normally but is forbidden from winning any trick. Contract
  base value **500** points. Solo Slam outranks any numeric bid, but it
  **cannot be announced after a Slam** — once a Slam is on the table, the
  auction is closed to further contract bids (asymmetric block). *Contre*
  and *surcontre* remain available.
- **Pass** (*passer*). A player who passes may re-enter the bidding later, as
  long as the auction has not yet ended.
- **Contrer** (double) — see §5.3.
- **Surcontrer** (redouble) — see §5.3.

### 5.3. Doubling

- **Contre**: an opponent of the current bidder may call *contre* instead of
  passing or bidding. This **freezes** the auction at the current contract
  and **doubles** the contract's point value (both for success and for
  failure).
- **Surcontre**: the bidder's team may respond to a contre with a
  *surcontre*, which **quadruples** the contract's point value. Either player
  on the bidding team may do this.
- *Contre* can only be called on the most recent numeric bid (it cannot be
  used to reopen a finished auction).

### 5.4. End of bidding

The auction ends when three consecutive players pass after the last bid (or
fewer if the bid has been contred / surcontred and the appropriate replies
given).

- The team holding the final bid becomes the **declarer** / *attaque* /
  *preneur*.
- The other team is the **defense** / *défense*.
- The suit of the final bid is the **trump** for this round.
- If everyone passes without anyone bidding, the round is annulled, cards are
  collected and redealt (with the same dealer).

---

## 6. Phase 3 — Card play

The first card of the round (the *entame*) is played by the player to the
**right of the dealer**, regardless of which team won the contract.

### 6.1. The trick

Each trick has 4 cards, one per player, played anticlockwise. The winner of a
trick leads the next one. There are 8 tricks per round.

### 6.2. Card-play obligations (in order)

The legal-move rules of Contrée are stricter than most card games. Given the
suit led, a player must obey the following, in order:

1. **Follow suit.** If you have any card in the led suit, you must play one.
2. **Trump if you cannot follow.** If you have no card in the led suit, you
   must play a trump — *unless* exception 4 applies.
3. **Overtrump if a trump has been played to this trick.** If trumps have
   already been played and you must trump, you must play a trump *higher* than
   the highest trump already on the table, if you have one. Otherwise play any
   trump.
4. **Partner exception.** If your partner is currently winning the trick
   (their card is the strongest played so far), you are *not* obligated to
   trump or to overtrump. You may discard freely.
5. **Discard.** If you have neither the led suit nor a trump (and no obligation
   forces a trump), you may play any card.

### 6.3. Special case: trump is led

When trump is led, the follow-suit rule (1) applies as usual. In addition,
every player who can must play a trump *higher* than the highest already on
the table, if they hold one. If they cannot beat it, they must still play a
trump.

### 6.4. Winning a trick

- If the trick contains any trumps, the highest trump wins.
- Otherwise, the highest card *in the led suit* wins. Cards of other non-trump
  suits cannot win.

### 6.5. Belote / Rebelote

If a player holds **both** the King and the Queen of trump, they may declare
this for a 20-point bonus to their team. The declaration is verbal:

- Say "**Belote**" when playing the first of the two cards.
- Say "**Rebelote**" when playing the second.

Notes:

- The bonus is awarded to the team regardless of which of the two cards is
  played first.
- It counts toward the contract total (so it can save a borderline contract).
- It is **kept even if the contract fails**. This is non-obvious and worth
  testing carefully in the engine.

---

## 7. Phase 4 — Scoring

### 7.1. Counting

At the end of the 8 tricks:

1. Each team sums the point values of the cards in the tricks it has won
   (using the *current* trump values — see §3).
2. The team that won the last trick adds the **dix de der** (10 points).
3. Belote bonus (20) is added if applicable.

The total across both teams (excluding Belote) is always **162**.

### 7.2. Contract outcome

Let:

- `C` = numeric contract value (one of 80, 90, …, 180)
- `P_attack` = points realized by the declaring team (cards + der + Belote if
  applicable)
- `M` = multiplier: 1 (no contre), 2 (contre), 4 (surcontre)

#### Numeric contracts (80–180)

**Contract made** (`P_attack ≥ C`):

- **Declarer** scores `(C + P_attack) × M`.
- **Defense** scores their own card points (no multiplier on defense's score
  in the standard ruleset).

Worked example: contract `90 ♥`, declarer realizes 102 → declarer 192,
defense 60.

**Contract failed** (`P_attack < C`), also called *chuté*:

- **Declarer** scores 0 (except for a Belote bonus, which is always preserved).
- **Defense** scores `(162 + C) × M`.

Worked example: contract `100 ♠`, failed → defense 262, declarer 0.

#### Slam and Solo Slam

Slam-family contracts keep the same shape as numeric contracts — the at-risk
amount is **contract + trick-points × multiplier** — but the trick pile
(normally up to 162) is *replaced* by a flat **substitute** equal to the
contract base. So the at-risk amount is:

> `(contract + substitute) × multiplier`

with `substitute = contract` for both Slam and Solo Slam.

| Bid       | Contract (`C`) | Substitute (replaces 162) | At-risk per `M`        |
| --------- | -------------- | ------------------------- | ---------------------- |
| Slam      | 250            | 250                       | `(250 + 250) × M`      |
| Solo Slam | 500            | 500                       | `(500 + 500) × M`      |

Both halves are multiplied by `M` (1 for normal, 2 for *contre*, 4 for
*surcontre*), giving:

| Contract  | Normal | Doubled | Redoubled |
| --------- | ------ | ------- | --------- |
| Slam      | 500    | 1000    | 2000      |
| Solo Slam | 1000   | 2000    | 4000      |

The grid is **symmetric**: whichever side wins the contract scores the
at-risk amount (declarer if made, defense if failed). The other side scores
zero (modulo Belote — see below).

**Slam** (*Capot*) is **made** when the declaring team wins **all 8 tricks**.
Anything less is a failure → defense scores the at-risk amount.

**Solo Slam** (*Capot général*) is **made** only when the **declaring player
personally** wins every one of the 8 tricks. The team winning all 8 together
is **not** enough — if the partner wins any trick, the Solo Slam fails and
defense scores the at-risk amount.

**Belote (+20)** still applies on top of the Slam grid: it goes to whichever
team holds the K + Q of trump, independent of which side wins the contract.

**Dix de der** does **not** apply on a Slam-family round — the substitute
already covers the full trick pile.

### 7.3. Double/ Redouble multiplier

The multiplier `M` from §7.2 applies whether the contract is made or failed.
Doubling cuts both ways — it punishes overbidding *and* rewards a successful
defense.

---

## 8. End of game

- A target score is agreed before the game (typical: **1500** or **2000**).
- The first team to reach or exceed the target at the end of a round wins.
- If both teams cross the target in the same round, the higher score wins.

---

## 9. Variants

These are common community variants. The base ContrAI engine does **not**
implement them; they are listed here for future reference and to clarify
terminology.

- **Sans atout** (*no trump*): a contract played with no trump suit. Card
  values shift (e.g. the Ace ranks highest in every suit) and the contract
  value is typically scaled.
- **Tout atout** (*all trump*): every suit acts as trump simultaneously. Card
  values become the trump values in every suit; total card points change
  accordingly.
- **Corsica deal**: 4-4 dealing pattern instead of 3-2-3.
- **Générale**: a regional synonym (or close cousin) of *Capot général* —
  a contract declaring the bidder *alone* will take all 8 tricks. ContrAI
  models this as **Solo Slam** in the canonical engine (see §5.2).
- **Annonces**: extra bonuses declared at the start of the first trick for
  card combinations held in hand (*tierce*, *cinquante*, *cent*, *carré*…).
  Inherited from classical Belote. **Out of scope for ContrAI** — this is
  what distinguishes contrée (without annonces) from coinche.

---

## 10. Terminology — FR ↔ EN

For the bilingual report and for keeping Claude consistent across languages.

| French                  | English                       | Notes                                                                   |
| ----------------------- | ----------------------------- | ----------------------------------------------------------------------- |
| Atout                   | Trump                         |                                                                         |
| Annonce                 | Bid / announcement            | Context: a bidding announcement (the only meaning used in this project) |
| Belote                  | Belote                        | The K+Q-of-trump bonus                                                  |
| Capot                   | Slam                          | Taking all 8 tricks (the *team* wins them all)                          |
| Capot général           | Solo Slam                     | Bidder *personally* takes all 8 tricks (cannot follow a Slam)           |
| Chute / Chuter          | Failure / to fail             | Used when the declarer does not make the contract                       |
| Contrat                 | Contract                      | The bid value                                                           |
| Contre / Contrer        | Double / to double            |                                                                         |
| Coupe / Couper          | Trump (n.) / to trump (v.)    | *Couper* = play a trump on a non-trump-led trick                        |
| Défausse / Se défausser | Discard / to discard          |                                                                         |
| Défense                 | Defense                       | The non-declaring team                                                  |
| Der / Dix de der        | Last trick / last-trick bonus | 10 points                                                               |
| Donneur                 | Dealer                        |                                                                         |
| Entame / Entamer        | Lead / to lead                | First card of a trick                                                   |
| Fournir                 | To follow suit                |                                                                         |
| Levée                   | Trick                         | Synonym of *pli*                                                        |
| Main                    | Hand                          | The 8 cards a player holds                                              |
| Manche                  | Round / hand                  | One complete deal + bidding + 8 tricks + scoring                        |
| Maître / Maîtresse      | Master                        | A card guaranteed to win (in its suit, given what has fallen)           |
| Monter                  | To raise / to overtrump       | *Monter à l'atout* = play a higher trump                                |
| Partie                  | Game                          | Multiple rounds, ending when a team reaches the target score            |
| Passer                  | To pass                       |                                                                         |
| Pli                     | Trick                         | Synonym of *levée*                                                      |
| Preneur / Prenante      | Declarer / declaring team     | The team that won the contract                                          |
| Rebelote                | Rebelote                      | Second of the Belote pair                                               |
| Sans atout              | No trump                      | Variant                                                                 |
| Surcontre / Surcontrer  | Redouble / to redouble        |                                                                         |
| Surcouper               | To overtrump                  |                                                                         |
| Tout atout              | All trump                     | Variant                                                                 |
| Valet                   | Jack                          | Top trump card                                                          |

---

## 11. Bidding convention — the 80-to-160 table

This is the community convention currently encoded in the engine's rule-based
AI. It is a **convention**, not a rule of the game: other tables exist and
players adapt.

The table tells you, given your hand, what is the highest opening contract you
can reasonably announce. Read each row as: *"If your hand contains at least
the listed pieces, you can open at this level."*

> The auction itself allows numeric bids up to **180** (see §5.2), but this
> opening-bid convention conservatively caps at 160 — 170 and 180 are
> Belote-only steps and the table here doesn't try to characterise hands
> strong enough to open there.

| Opening | Required trumps | Min trumps | Aces | Non-bare tens | Min tricks | Belote |
| ------- | --------------- | ---------- | ---- | ------------- | ---------- | ------ |
| 80      | J ⊕ 9 (one of)  | 3          | 1    |               | 4          |        |
| 90      | J ∧ 9 (both)    | 3          | 1    |               | 4          |        |
| 100     | J ⊕ 9           | 3          | 2    |               | 5          |        |
| 110     | J ∧ 9           | 3          | 2    |               | 5          |        |
| 120     | J ⊕ 9           | 3          | 3    |               | 6          |        |
| 130     | J ∧ 9           | 3          | 3    |               | 6          |        |
| 140     | J ⊕ 9           | 4          | 3    | 1             | 6          | ✅      |
| 150     | J ∧ 9           | 4          | 3    | 1             | 6          | ✅      |
| 160     | J ∧ 9 ∧ A       | 5          | 3    | 2             | 7          | ✅      |

Where:

- `J ⊕ 9` means *Jack XOR 9 of trump* (at least one, possibly both).
- `J ∧ 9` means *Jack AND 9 of trump*.
- "Min trumps" is the total trump count *including* J and 9.
- "Aces" counts aces *outside* the trump suit (external aces).
- "Non-bare tens" means tens of non-trump suits that are protected (not
  singletons).
- "Belote" ✅ means holding K+Q of the proposed trump is required.

### 11.1. Choosing the suit

If the hand qualifies at the same level for multiple suits, the AI chooses:

1. The suit with the strongest expected take (most aces / tens that fit).
2. Tie-break on **Belote** (favor the suit where you hold K+Q of trump).
3. Final tie-break (preference order): **♠ Spades > ♥ Hearts > ♦ Diamonds > ♣ Clubs**.

### 11.2. Bidding over partner

If your partner has already bid and you can add value, raise their contract
rather than start a new one in another suit:

- **+10** for each *external* ace you hold.
- **+10** if you hold the missing complement of trump (the J or 9 that partner
  may be missing) in the suit they announced.

If you cannot raise and cannot open in another suit, **pass**.

### 11.3. When to contre / surcontre

*To be expanded as the AI strategy evolves. For now: the rule-based AI
contres when its expected defensive points clearly exceed the contract
threshold; details live alongside the AI implementation.*

---

## 12. Quick reference — round flow

```
[Deal]      → 8 cards each, 3-2-3 anticlockwise
   ↓
[Bidding]   → starting right of dealer
              actions: bid (80–180, slam, or solo slam), contre, surcontre, pass
              ends: 3 consecutive passes after the last bid
   ↓
[Card play] → 8 tricks, anticlockwise, lead = right of dealer
              obey: follow → trump → overtrump (except partner-master) → discard
              optional: announce Belote/Rebelote on K/Q of trump
   ↓
[Scoring]   → sum cards + dix de der (+ belote if applicable)
              apply contract success/failure + multiplier
   ↓
[Check]     → if any team ≥ target (1500/2000): end game
              else next round, dealer rotates right
```

---

## 13. Open points

Things deliberately left out or unresolved here, to revisit:

- **Annonces** (tierce, cinquante, carré, etc.) are out of scope for ContrAI.
  This is the explicit boundary of the project: Contrée *without* annonces.
- The *sans atout* and *tout atout* variants are out of scope for v1 of the
  engine.
- The bidding table in §11 is one convention among several. The project's
  next AI families (supervised → RL) will likely *not* use this table at all;
  it remains here as the baseline rule-based behavior and as a sanity check
  against learned policies.
- The LaTeX report (`ContrAI.tex`) currently has the turn-direction wrong:
  it says *gauche du donneur* (left of dealer) in several places where the
  specs and the standard rules say *droite du donneur* (anticlockwise rotation,
  right of dealer plays next). Fix-up pending a separate proposal — this doc
  uses the correct version.
