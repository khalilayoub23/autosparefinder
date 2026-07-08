# AutoSpare Brand Voice

> The platform talks like an expert mechanic who also happens to have a PhD in AI.
> Confident, precise, never arrogant. Useful over impressive.

---

## Mission
To make the right automotive part findable, comparable, and deliverable — for anyone,
anywhere, in seconds.

## Vision
A world where no repair is delayed because of a missing part.

## Values
| Value | What it means in the UI |
|-------|------------------------|
| **Precision** | Numbers are exact. OEM codes are never approximated. |
| **Speed** | Search completes in <100ms. UX never makes users wait without feedback. |
| **Trust** | Prices are real. Stock is real. Supplier names are masked, not hidden. |
| **Intelligence** | AI enhances, doesn't replace. It surfaces what humans would miss. |
| **Access** | Global coverage. No brand bias. IL + every major market. |

---

## Tone

### Who we sound like
An experienced parts specialist at a premium dealership — one who:
- Knows every OEM number by heart
- Can cross-reference three brands in their head
- Speaks plainly to mechanics and precisely to engineers
- Never talks down to a novice, never condescends to an expert

### What we don't sound like
- ❌ SaaS cheerfulness ("Woohoo! Your part is on its way! 🎉")
- ❌ Corporate distance ("Your request has been submitted for processing")
- ❌ Overclaiming ("The most revolutionary automotive platform ever built")
- ❌ Vague AI hype ("Powered by next-generation artificial intelligence")

---

## Writing Rules

1. **Lead with the result, not the process**
   - ✅ "Found 23 compatible parts"
   - ❌ "We searched our database of 4.1M parts and found..."

2. **Numbers are precise — never round unless rounding is accurate**
   - ✅ "4,171,856 parts"
   - ❌ "Over 4 million parts"

3. **Active voice always**
   - ✅ "AutoSpare matched your VIN to 847 compatible parts"
   - ❌ "847 compatible parts were found by our system"

4. **Errors are honest and actionable**
   - ✅ "No parts found for OEM 04465-99999. Try the OEM without dashes, or check the number."
   - ❌ "An error occurred. Please try again."

5. **AI output is marked and explained, never hidden**
   - ✅ "[AI] Top match: Toyota OEM 04465-02250 — 97% fitment confidence"
   - ❌ "Recommended for you" (unexplained)

6. **Hebrew support**: All customer-facing strings available in English + Hebrew.
   Technical identifiers (OEM, VIN, SKU) remain in Latin regardless of language.

---

## Button Labels

| Context | Label |
|---------|-------|
| Primary search | Search Parts |
| AI search | Ask AI |
| VIN lookup | Scan VIN |
| Price comparison | Compare Prices |
| Add to cart | Add to Order |
| Buy now | Buy Now |
| Get quote | Request Quote |
| View details | View Part |
| Load more | Load More |
| Supplier action | Connect Supplier |
| Export data | Export CSV |
| Confirm action | Confirm |
| Cancel | Cancel |
| Delete (destructive) | Delete permanently |

**Rule:** Never use "Submit". Never use "Click here". Never use emojis in buttons.

---

## AI Personality

The AI assistant is called **AutoSpare AI** internally. It does not have a name
shown to users — it appears as the platform's intelligence, not a separate persona.

**Personality traits:**
- Responds in the user's language (Hebrew / English auto-detect)
- Cites specific OEM numbers, not descriptions alone
- Always states confidence level for recommendations
- Asks clarifying questions when year/model is ambiguous — never guesses
- Acknowledges when a part is not in catalog rather than recommending a wrong match

**AI response format:**
```
[Part name] — [OEM number]
Fits: [Vehicle range]
Price: ₪ [price] / Source: [masked]
Confidence: [%] match
[1-sentence explanation if non-obvious]
```

---

## Error Messages

| Error | Message |
|-------|---------|
| No search results | "No parts found for '[query]'. Check the OEM number or try a different description." |
| VIN decode failed | "Couldn't decode this VIN. Make sure all 17 characters are correct." |
| Out of stock | "Not currently available. We'll notify you when it's back." |
| Price unavailable | "Price unavailable from this source. Try Compare Prices for alternatives." |
| Connection error | "Having trouble connecting. Your session is saved — try refreshing." |
| Auth error | "That email or password isn't right. Try again or reset your password." |
| Payment failed | "Payment didn't go through. Check your card details or try a different method." |

---

## Product Naming

| Feature | Internal name | Customer-facing name |
|---------|---------------|---------------------|
| AI search | AI Search | AutoSpare AI |
| OEM cross-reference | Cross-ref engine | Find Compatible Parts |
| Supplier aggregator | supplier_aggregator | Price Comparison |
| VIN lookup | plate_lookup / NHTSA | VIN Search |
| Fitment engine | part_vehicle_fitment | Fits My Car |
| Catalog | parts_catalog | Parts Database |
| Supplier portal | supplier_portal | Supplier Dashboard |
| Price intelligence | Boaz (agent) | Price Intelligence |

**Rule:** Internal agent names (REX, Boaz, NIR) never appear in customer-facing UI.

---

## Microcopy Examples

| Moment | Copy |
|--------|------|
| Empty cart | "No parts added yet. Search above to get started." |
| Empty order history | "No orders yet. Your first order will appear here." |
| First VIN search | "Enter your 17-character VIN to see all compatible parts for your vehicle." |
| AI loading | "Searching 4.1M parts..." |
| Part added to cart | "Added to order" |
| Compatibility confirmed | "✓ Fits your Toyota Corolla 2020" |
| Low stock warning | "Only 2 left" |
| Best price indicator | "Best price" |
| Supplier masked note | "Supplier details are hidden to keep pricing fair" |
