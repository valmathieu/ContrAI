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

1. **Deal**
2. **Bidding**
3. **Card play** — 8 tricks
4. **Scoring**

The first team to reach a target score (commonly 1500 or 2000 points) wins.

---

## 2. Setup

- **Players.** Exactly 4.
- **Teams.** Two fixed pairs, partners seated opposite each other. By
  convention we call them **North–South** and **East–West** (N–S vs E–W), as
  in Bridge.
- **Deck.** 32 cards: 7, 8, 9, 10, Jack, Queen, King, Ace in each of four suits
  (♠ ♥ ♦ ♣).
- **Turn order.** Anticlockwise by default — the player to the **right** of the
  current actor plays next. Tables may agree to play clockwise instead; every
  directional rule in this document (dealing, bidding order, lead, dealer
  rotation) then mirrors accordingly.
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

### 3.3. All-trump contracts (strongest first, every suit)

Under an **all-trump** contract (§5.2) every suit ranks like trump, with a
dedicated scale so the deck total stays constant:

| Card       | Jack | 9   | Ace | 10  | King | Queen | 8   | 7   |
| ---------- | ---- | --- | --- | --- | ---- | ----- | --- | --- |
| **Points** | 14   | 9   | 6   | 5   | 3    | 1     | 0   | 0   |

Order within every suit: **V 9 A 10 R D 8 7** — 38 points per suit, 152 in the
deck, exactly as in a suit contract.

### 3.4. No-trump contracts (strongest first, every suit)

Under a **no-trump** contract every suit ranks like a plain suit, rescaled to
keep the deck total constant:

| Card       | Ace | 10  | King | Queen | Jack | 9   | 8   | 7   |
| ---------- | --- | --- | ---- | ----- | ---- | --- | --- | --- |
| **Points** | 19  | 10  | 4    | 3     | 2    | 0   | 0   | 0   |

Order within every suit: **A 10 R D V 9 8 7**.

### 3.5. Totals

- 152 points live in the cards themselves — under **every** contract mode
  (suit, all-trump, no-trump).
- An extra 10 points (the **last trick bonus**) go to whichever team wins the
  last trick.
- Per round, **162 points** are distributed across the two teams.
- Belote (see §6.6) adds 20 points per belote where it exists: exactly one is
  possible in a suit contract (per-round ceiling 182); none at no-trump; at
  all-trump the agreed belote regime (§6.6) allows zero, one, or up to four
  (ceiling 242).

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

- **Make a bid.** Announce a *value* and a *trump choice* — one of the four
  suits (e.g. `90 ♥`), **no trump**, or **all trump**. The value is the number
  of points your team commits to taking under that trump regime.
  
  - Minimum opening bid: **80**.
  - Increments: **10 points**.
  - Each new bid must be strictly higher than the current one, whatever its
    trump choice.
  - Maximum numeric bid — the ladder top depends on the trump choice and, at
    all trump, on the agreed belote regime (§6.6):
  
  | Trump choice                        | Ladder top | Theoretical max take  |
  | ----------------------------------- | ---------- | --------------------- |
  | Suit (♠ ♥ ♦ ♣)                      | 180        | 162 + 20 Belote = 182 |
  | No trump                            | 160        | 162 (no Belote)       |
  | All trump — no-belote regime        | 160        | 162                   |
  | All trump — single belote (default) | 180        | 162 + 20 = 182        |
  | All trump — four belotes            | 240        | 162 + 80 = 242        |
  
  > The top steps of each ladder are only feasible with the required **Belote**
  > in hand — the cards alone cap at 152 + 10 = 162. The auction does **not**
  > enforce that at bid time: announcing a top step without the Belote is legal
  > but commits the bidder to a contract they cannot make on cards alone, which
  > will *fail* at scoring.

- **Bid Slam**. A special bid declaring your team will take **all 8
  tricks**. Contract base value **250** points. Slam outranks any numeric
  bid: once declared, no further contract bid is legal (numeric, Slam, or
  Solo Slam). *Double* and *redouble* remain available against a Slam.

- **Bid Solo Slam**. A stronger all-tricks bid declaring
  that the **bidder personally** will win every one of the 8 tricks — their
  partner may play normally but is forbidden from winning any trick. Contract
  base value **500** points. Solo Slam outranks any numeric bid, but it
  **cannot be announced after a Slam** — once a Slam is on the table, the
  auction is closed to further contract bids (asymmetric block). *Double* and *redouble* remain available.

> Slam-family bids exist under every trump choice (suit, no trump, all trump),
> with the same base values. Some tables disallow the **Solo Slam** bid
> altogether — its availability is agreed before the game (allowed by default).

- **Pass**. A player who passes may re-enter the bidding later, as
  long as the auction has not yet ended.

- **Double** — see §5.3.

- **Redouble** — see §5.3.

### 5.3. Doubling

- **Double**: an opponent of the current bidder may call *contre* instead of
  passing or bidding. This **freezes** the auction at the current contract
  and **doubles** the contract's point value (both for success and for
  failure).
- **Redouble**: the bidder's team may respond to a contre with a
  *surcontre*, which **quadruples** the contract's point value. Either player
  on the bidding team may do this.
- *Double* can only be called on the most recent numeric bid (it cannot be
  used to reopen a finished auction).
- **Intervening passes do not close the Double/ Redouble window.** Both
  *double* (by an opposing player) and *redouble* (by the bidding team)
  remain legal up until the auction terminates on three consecutive passes
  per §5.4 — players who passed earlier may re-enter and call *double* or
  *redouble*, consistent with the general re-entry rule in §5.2.
- **Table option:** doubling and redoubling **Slam-family** contracts can be
  forbidden by prior agreement (allowed by default).

### 5.4. End of bidding

The auction ends when three consecutive players pass after the last bid (or
fewer if the bid has been contred / surcontred and the appropriate replies
given).

- The team holding the final bid becomes the **declarer** / *attacker* .
- The other team is the **defense**.
- The suit of the final bid is the **trump** for this round.
- If everyone passes without anyone bidding, the round is annulled: cards are
  collected and the **next dealer** (normal rotation, §2) redeals.

---

## 6. Phase 3 — Card play

The first card of the round (the *lead*) is played by the player to the
**right of the dealer**, regardless of which team won the contract.

> **Table option — the Solo Slam gives the lead** (off by default): the declarer
> of a Solo Slam contract leads the first trick instead. Nothing else changes —
> trick winners still lead the following tricks, and the next round's dealer is
> still the seat after the previous dealer, never the Solo Slam declarer.

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

> **Table option — under-trump exemption** (*dispense de pisser*, off by
> default): when you are void in the led suit, an **opponent** has already
> trumped, and you hold no trump able to beat the best trump on the table, you
> may discard freely instead of playing a losing trump. The obligations to
> over-trump when you can, to trump when nobody has cut yet, and the trump-led
> rule (§6.3) are unchanged.

### 6.3. Special case: trump is led

When trump is led, the follow-suit rule (1) applies as usual. In addition,
every player who can must play a trump *higher* than the highest already on
the table, if they hold one. If they cannot beat it, they must still play a
trump.

### 6.4. No-trump and all-trump play

- **No trump:** there is no trump suit. Follow the led suit if you can;
  otherwise discard freely — no trumping, no over-trumping, no partner
  exception to worry about. The highest card of the led suit (§3.4 order) wins
  the trick.
- **All trump:** every suit is trump, and every trick behaves like §6.3 in the
  led suit: follow it *and* play a card **higher** than the best one already on
  the table if you can; if you cannot beat it, you must still follow. When you
  are void in the led suit you discard freely — cards of the other suits can
  never win the trick, so there is no cross-suit cutting. The highest card of
  the led suit (§3.3 order) wins.

### 6.5. Winning a trick

- If the trick contains any trumps, the highest trump wins.
- Otherwise, the highest card *in the led suit* wins. Cards of other non-trump
  suits cannot win.

These two rules cover suit contracts; §6.4 states the winner at no trump and
all trump (always the highest card of the led suit).

### 6.6. Belote / Rebelote

If a player holds **both** the King and the Queen of trump, they may declare
this for a 20-point bonus to their team. The declaration is verbal:

- Say "**Belote**" when playing the first of the two cards.
- Say "**Rebelote**" when playing the second.

Notes:

- The bonus is awarded to the team regardless of which of the two cards is
  played first.
- **Where Belote exists.** Suit contracts: only K + Q of the trump suit —
  exactly one belote possible. No trump: no belote at all. All trump: the table
  agrees a *belote regime* before the game — **none**, **single** (only the
  first belote declared in play counts; the common default), or **four** (every
  K + Q pair held counts 20 each, so one team may score up to +80).
- **It counts toward the contract** by default — both for reaching the contract
  value and for out-scoring the defense where that rule applies (§7.5): *la
  belote sert à prendre*. Tables may agree instead that Belote never enters
  those tests and is only added to the marked score afterwards.
- It is **kept even if the contract fails** by default. This is non-obvious and
  worth testing carefully in the engine. A table option transfers the failing
  attackers' Belote (20 points each) to the defense instead — a defending
  team's Belote is never taken.

---

## 7. Phase 4 — Scoring

### 7.1. Counting

At the end of the 8 tricks:

1. Each team sums the point values of the cards in the tricks it has won
   (using the *current* trump values — see §3).
2. The team that won the last trick adds the **last trick bonus** (10 points).
3. Belote bonus (20) is added if applicable.

The total across both teams (excluding Belote) is always **162**.

### 7.2. Contract outcome

Let:

- `C` = numeric contract value (80, 90, … up to the ladder top of the trump
  choice — §5.2)
- `P_attack` = points realized by the declaring team (cards + der + Belote if
  applicable)
- `M` = multiplier: 1 (no double), 2 (double), 4 (redouble)

#### Numeric contracts

**Un-doubled** (`M = 1`) — the two sides *share* the pile:

- **Made** (`P_attack ≥ C`): **declarer** scores `C + P_attack`; **defense**
  scores its own card points (its share of the 162 + the *last trick bonus* if it
  took the last trick).
  Worked example: contract `90 ♥`, declarer realizes 102 → declarer 192,
  defense 60.
- **Failed** (`P_attack < C`, *failed*): **declarer** scores 0; **defense**
  scores `160 + C`.
  Worked example: contract `100 ♠`, failed → defense 260, declarer 0.

**Doubled / redoubled** (`M > 1`) — **winner-takes-all**, exactly like the
Slam grid below:

- The **winning side** (declarer if the contract is made, defense if it is
  failed) scores `160 + C × M`. The stake is the *same* whichever side wins.
- The **losing side scores 0** — the defense never keeps its own card points
  once it has doubled.
  Worked example: contract `100 ♥ ×2` made → declarer 360, defense 0; the same
  contract failed → defense 360, declarer 0.

> The 162-point pile is treated as a flat **160** in the winner-takes-all and
> *failed* formulas — the engine's rounding convention.

**Table option — fixed stakes** (the *modular* stakes above are the default):
the at-risk amounts become flat, independent of `C` — failed un-doubled **320**,
doubled **640**, redoubled **1280**. Slam-family contracts under this option:
1000 / 2000 doubled / redoubled for a Slam, 2000 / 4000 for a Solo Slam;
un-doubled Slam-family amounts are unchanged (500 / 1000).

**Belote (+20)** is the standing exception to "the loser scores 0": it is
always credited to the team **holding** K + Q of trump (not whoever captures
those cards in a trick — see §6.6), on top of everything else, win or lose.

#### Unannounced slam

If the declaring team wins **all 8 tricks** on a numeric contract *without
having bid a Slam*, the trick pile (152 cards + 10 *last trick bonus* = 162) is
replaced by a flat **250** substitute: the declarer scores `C + 250`, the
defense scores nothing, and the contract is necessarily **made** (sweeping
every trick cannot fail). This mirrors the announced-Slam shape but keeps the
numeric contract value `C` as the base.

- If the team split the 8 tricks, the substitute is **250**. If the
  contracting player swept all 8 **personally**, the substitute is **500** by
  default — tables may agree to keep a flat 250 for both cases.
- **Un-doubled only.** A doubled / redoubled sweep keeps the winner-takes-all
  `160 + C × M` shape above; the 250 substitute does **not** apply.
  - **Declaring team only.** If the *defense* takes all 8 tricks the declarer
    has simply failed — score it as an ordinary failed contract (`160 + C`),
    not as a slam.
- **Belote (+20)** still layers on top for the holding team, as everywhere.

> Worked example: contract `100 ♠`, declaring team sweeps all 8 → declarer
> 350 (`100 + 250`), defense 0; if the contracting player took them all alone →
> 600 (`100 + 500`).

#### Slam and Solo Slam

Slam-family contracts keep the same shape as numeric contracts — the at-risk
amount is **contract × multiplier + substitute** — where the trick pile
(normally up to 162) is *replaced* by a flat **substitute** equal to the
contract base:

> `C × M + substitute`

with `substitute = C` for both Slam and Solo Slam.

| Bid       | Contract (`C`) | Substitute (replaces 162) | At-risk per `M` |
| --------- | -------------- | ------------------------- | --------------- |
| Slam      | 250            | 250                       | `250 × M + 250` |
| Solo Slam | 500            | 500                       | `500 × M + 500` |

Only the contract half is multiplied by `M` (1 for normal, 2 for double, 4 for
redouble) — the substitute is not — giving:

| Contract  | Normal | Doubled | Redoubled |
| --------- | ------ | ------- | --------- |
| Slam      | 500    | 750     | 1250      |
| Solo Slam | 1000   | 1500    | 2500      |

Under the **fixed stakes** option (see the numeric-contracts section) the
doubled / redoubled amounts are flat 1000 / 2000 for a Slam and 2000 / 4000
for a Solo Slam instead.

The grid is **symmetric**: whichever side wins the contract scores the
at-risk amount (declarer if made, defense if failed). The other side scores
zero (modulo Belote — see below).

**Slam** is **made** when the declaring team wins **all 8 tricks**.
Anything less is a failure → defense scores the at-risk amount.

**Solo Slam** is **made** only when the **declaring player
personally** wins every one of the 8 tricks. The team winning all 8 together
is **not** enough — if the partner wins any trick, the Solo Slam fails and
defense scores the at-risk amount.

**Belote (+20)** still applies on top of the Slam grid: it goes to whichever
team holds the K + Q of trump, independent of which side wins the contract.

**Last trick bonus** does **not** apply on a Slam-family round — the substitute
already covers the full trick pile.

### 7.3. Marking conventions

§7.2 describes the common default, where **both** marking conventions are
active: *points faits* (the points actually made are marked) **and** *points
annoncés* (the contract value is marked). Tables may keep only one:

| Convention             | Made contract                               | Failed contract                                                         |
| ---------------------- | ------------------------------------------- | ----------------------------------------------------------------------- |
| Both (default)         | declarer `C + P_attack`; defense its points | defense `160 + C` (flat 320 under fixed stakes)                         |
| *Points faits* only    | declarer `P_attack`; defense its points     | defense the whole pile: **162** (Slam-family: the 250 / 500 substitute) |
| *Points annoncés* only | declarer `C`; defense **0**                 | defense flat **160** (Slam-family: 250 / 500)                           |

Doubled / redoubled contracts ignore the marking conventions entirely — the
winner-takes-all stake of §7.2 applies as-is. Belote follows its own rules
(§6.6) on top of every convention.

### 7.4. Rounding

By default marks are written exactly. Tables may agree to round each team's
realized card points to the nearest **10** or the nearest **5** when composing
the marks:

- Nearest 10 rounds half **up**: 85 → 90. A raw 85–77 split therefore marks
  90–80 — exceptionally 170 in total.
- With integer piles, ties are impossible when rounding to the nearest 5.
- Rounding affects the *marks* only; whether the contract is made is always
  judged on exact points.

### 7.5. Out-scoring the defense & dispute (*litige*)

Table option (off by default): to make its contract the attack must not only
reach `C` but also score **strictly more than the defense**
(`P_attack > P_defense`), Belote included on both sides whenever it counts
toward the contract (§6.6). An exact tie — 81 / 81, or 91 / 91 when a Belote
counts — is a **dispute** (*litige*), resolved by prior agreement in one of
three ways:

1. Each team marks its own points (the common default).
2. Only the defense marks its points; the attack marks nothing.
3. The attack fails — ordinary failed-contract scoring applies.

### 7.6. Double/ Redouble multiplier

The multiplier `M` from §7.2 applies whether the contract is made or failed.
Doubling cuts both ways — it punishes overbidding *and* rewards a successful
defense.

---

## 8. End of game

- A target score is agreed before the game (typical: **1500** or **2000**).
- The first team to reach or exceed the target at the end of a round wins.
- If both teams cross the target in the same round, the higher score wins.
- If both teams sit at the **same score** at or above the target, nobody has
  won yet: play continues with additional rounds (sudden death) until one
  team leads, in case of a *dispute*.
- **Table option** (off by default): a team cannot cross the target on Belote
  points alone — if only the Belote bonus takes it past the target, the win
  waits until points from play confirm it.

---

## 9. Variants

Community variants and where they stand in this rule set:

- **No trump** and **All trump**: fully specified in this document (§3.3–3.5,
  §5.2, §6.4, §6.6) and part of the canonical rule set.
- **Corsica deal**: 4-4 dealing pattern instead of 3-2-3. Not part of the rule
  set; listed for terminology.
- **Solo Slam**: a contract declaring the bidder *alone* will take all 8
  tricks — a standard bid here (§5.2), optional at some tables.
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
| Annonce                 | Bid                           | Context: a bidding announcement (the only meaning used in this project) |
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
| Enchères                | Auction                       |                                                                         |
| Entame / Entamer        | Lead / to lead                | First card of a trick                                                   |
| Fournir                 | To follow suit                |                                                                         |
| Générale                | Solo Slam                     | Regional synonym of *capot général*                                     |
| Levée                   | Trick                         | Synonym of *pli*                                                        |
| Litige                  | Dispute                       |                                                                         |
| Main                    | Hand                          | The 8 cards a player holds                                              |
| Manche                  | Round / hand                  | One complete deal + bidding + 8 tricks + scoring                        |
| Maître / Maîtresse      | Master                        | A card guaranteed to win (in its suit, given what has fallen)           |
| Monter                  | To raise / to overtrump       | *Monter à l'atout* = play a higher trump                                |
| Partie                  | Game                          | Multiple rounds, ending when a team reaches the target score            |
| Passer                  | To pass                       |                                                                         |
| Pisser                  | To under-trump                | Play a losing trump by obligation                                       |
| Pli                     | Trick                         | Synonym of *levée*                                                      |
| Points annoncés         | Announced-value marking       | Marking convention (§7.3)                                               |
| Points faits            | Actual-points marking         | Marking convention (§7.3)                                               |
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
> strong enough to open there. It also covers **suit contracts** only —
> opening conventions for no-trump and all-trump are not codified yet.

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

```text
[Deal]      → 8 cards each, 3-2-3 anticlockwise
   ↓
[Bidding]   → starting right of dealer
              actions: bid (80 up to the mode ladder — suit / no-trump /
              all-trump), slam, solo slam, contre, surcontre, pass
              ends: 3 consecutive passes after the last bid
   ↓
[Card play] → 8 tricks, anticlockwise, lead = right of dealer
              obey: follow → trump → overtrump (except partner-master) → discard
              optional: announce Belote/Rebelote on K/Q of trump
   ↓
[Scoring]   → sum cards + last trick bonus (+ belote if applicable)
              apply contract success/failure + multiplier
   ↓
[Check]     → if one team strictly leads at ≥ target (1500/2000): end game
              tie at ≥ target: sudden death, keep playing until one team leads
              else next round, dealer rotates right
```

---

## 13. Open points

Things deliberately left out or unresolved here, to revisit:

- **Annonces** (tierce, cinquante, carré, etc.) are out of scope for ContrAI.
  This is the explicit boundary of the project: Contrée *without* annonces.
- The bidding table in §11 is one convention among several. The project's
  next AI families (supervised → RL) will likely *not* use this table at all;
  it remains here as the baseline rule-based behavior and as a sanity check
  against learned policies.
- The LaTeX report (`ContrAI.tex`) currently has the turn-direction wrong:
  it says *gauche du donneur* (left of dealer) in several places where the
  specs and the standard rules say *droite du donneur* (anticlockwise rotation,
  right of dealer plays next). Fix-up pending a separate proposal — this doc
  uses the correct version.
