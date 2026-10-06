# Horse in the Hospital: production bible

The build session reads this first. Every decision below was made on purpose; change one only if the
footage proves it wrong, and note the change at the bottom.

## The goal

A ~3:30 music video that honors John Mulaney's "Horse in a Hospital" bit. Joel will share it with a few
people as a comedy piece and as a showpiece. It has to be funny on first watch, look expensive, and land
one honest beat without preaching.

The thesis, never stated out loud: nobody knows what the horse will do next, least of all the horse, and
the rest of us are standing in the waiting room watching the horse on TV while the actual horse is
right behind us eating the ficus. The mirror points at everyone: the brunch people, the people who
opened the door, and the narrator himself ("I wasn't paying attention").

## Tone rules

1. Play it completely straight. Prestige medical drama, shot like a serious film. The horse is the only
   absurd thing in frame, and nobody in the film acts like it's a bit. Deadpan is the engine.
2. Never decode the metaphor. No orange hair, no red ties, no flags, no politicians' faces. The horse is a
   horse, the hippo is a hippo. The bit never names anyone beyond "this guy," and neither do we.
3. No real people's likenesses (Mulaney, Busey, Andy Cohen, anyone). Names may appear in sung lyrics only.
4. No harm shown. The horse charges toward the NICU and is stopped by a nurse with a crash cart; the hippo
   threatens and yawns. Chaos is paperwork, IV poles, hay, and staff morale.
5. Cable-news bleeps are the default cut (in-world, funnier, shareable with anyone). Also deliver an
   uncensored cut.
6. Cut on purpose: the N-word line, the new-Nazis closer. Sleepover kid is the post-credits stinger.
7. Lyrics on screen only for choruses and emotional lines (Joel's taste), serif italic. Cable-news chyrons
   and the ticker carry the other text jokes.

## Length and structure

Target 3:30, hard cap 3:50. Suno is the spine (~3:30); my inserts add ~15 s; trim in post if anything sags
(brunch first, then the outro).

| Section | What happens | Audio source |
| --- | --- | --- |
| Intro | Night exterior, St. Eligius Memorial, rain. Empty corridor, one hoof print in spilled coffee. Title card. | Suno |
| Verse 1 | Waiting room: everyone furious at screens. "Sticky": a shoe stuck to the floor. "It's like..." elevator doors | Suno |
| Chorus 1 | Doors open: THE HORSE. Slow-mo majesty under fluorescents. Close on the confused eye. | Suno |
| Verse 2 | Cable news split screen: anchor + the bird-in-airport expert at a gate. | Suno + news sting SFX |
| Verse 3 | Staff around the nurses' station TV. Horse rides the elevator, calm. Floor indicator. | Suno + elevator ding |
| Break | Operating room freezes. Heart monitor. Shadow passes the frosted door. | Suno, then INSERT 2-4 bars of hoof clops on linoleum over room tone |
| Verse 4 | Horse peacefully eating a get-well bouquet. Staff exhale. Chyron: HORSE CALM, SOURCES SAY | Suno |
| Drop | Horse rears, gallops toward double doors marked NICU. Nurse slams a crash cart across the doors. | Suno + gallop SFX |
| Stop-time | Horse skids to a halt in a drift of paperwork, looks at camera. Bleep over the word, black bar on the lyric. | Suno + bleep |
| Verse 5 | Overexposed brunch, linen, mimosas. "We're well past that": horse in the cafeteria line with a tray. | Suno |
| Verse 6 | Globe graphic, dotted line 5,000 miles to a mud lake. Hippo half-submerged, staring. | Suno |
| Instrumental | HORSE vs HIPPO, wrestling-promo split screen with stat chyrons. | Suno stems + INSERT ElevenLabs Horse and Hippo promo (extend bars if needed) |
| "Okay." | The waiting room, deflated, blank. One man slowly sips coffee. The Nurse looks at camera. | Suno |
| Verse 7 | Horse-catcher's heroic slow walk with a lasso and lanyard; then carrying a cardboard box out past security. A colleague in a plain tricorn hat looks confused. | Suno |
| Bridge | THE MIRROR: everyone glued to screens showing the horse; the horse behind them eating the ficus. Automatic door "WELCOME". "NOW SERVING 4" ticket machine. Nanny cam. | Suno |
| Silence | After "Gary Busey..." one bar of black and a single hoof clop. | Suno silence + clop |
| Final Chorus | Key change. Atrium parade: staff line the balconies, paperwork confetti, sunrise. Everything converges. "Neither do your parents": elderly couple in gowns, synchronized shrug. | Suno |
| Outro | Dawn. Horse alone at a window, looking out. Turns to camera. End card with credit. | Suno |
| Stinger | Black. Clock 11:59 to 12:00. Kid: "It's tomorrow now." (or: horse in elevator, doors close, muzak chorus, ding) | ElevenLabs |

End card: "After 'Horse in a Hospital' by John Mulaney (Kid Gorgeous at Radio City, 2018)."

## Recurring cast (keep these descriptions verbatim in every image prompt)

- **The Horse**: a large chestnut thoroughbred with a white blaze down its face, four white socks, a dark
  mane, and a blue hospital patient wristband around its left foreleg printed "HORSE". Expression: mild,
  sincere confusion. Make a reference sheet first and pass it to every generation.
- **The Nurse** (audience surrogate, our Jim-from-The-Office): night-shift charge nurse in her fifties,
  navy scrubs, reading glasses on a beaded chain, grey-streaked bun, unflappable. Her looks to camera are
  the laughs. She is the "Okay."
- **The Hippo**: an enormous grey-pink hippopotamus half-submerged in a brown lake of mud, scarred snout,
  tiny twitching ears. Only ever seen on screens, in grainy long-lens "foreign feed" footage.
- **The Bird Man** (expert): earnest man in a fleece vest at an airport gate, pigeon on the departures sign.
- **The Horse-catcher**: rugged man with a coiled lasso and a hospital ID lanyard, Armageddon walk.

Setting: St. Eligius Memorial (patron saint of horses; also the hospital in St. Elsewhere).

## Look

Photoreal, prestige drama: shot on ARRI Alexa, anamorphic 2.39:1, cool teal fluorescent light against
warm practicals, shallow depth of field, 35 mm grain, motivated lighting, symmetrical corridors. The
cable-news layer is the opposite: flat, bright, chyron red and white, rendered by me in code (crisp text,
no AI lettering). Lyrics: serif italic, bottom third, choruses and emotional lines only.

## Audio plan

- Suno keeper -> stems (Demucs: vocals, drums, bass, other) so I can mute the band for the break, duck
  under dialogue, and strip any vocals Suno puts on the instrumental break.
- Inserts cut on downbeats from `analysis.json`; extend the instrumental break by repeating bars if the
  promo dialogue needs more room.
- ElevenLabs v3 voices (designed, never cloned from a real person): Anchor, Bird Man, Horse (unhinged
  wrestling-promo growl), Hippo (huge, wet, slow; pitch down slightly), Hospital PA, Sleepover Kid.
  Joel may supply his own cloned voice ID for the Bird Man cameo.
- ElevenLabs sound effects: hooves on linoleum (walk, trot, gallop), elevator ding and doors, heart
  monitor beeps in tempo, whinny, snort, hippo bellow, mud bubbles, crowd gasp, news sting, PA chime,
  surgical tray clatter.
- Bleep: 1 kHz sine, generated locally. Two masters: bleeped and uncensored.
- Every TTS line checked with Whisper before it goes in. Master to -14 LUFS, true peak -1 dBTP.

## Picture plan

- Stills: Gemini image model (Nano Banana) with the Horse and Nurse reference sheets for consistency.
- Motion: image-to-video on the shots that need it (gallops, elevator doors, the rear-up, the atrium
  parade, the hippo yawn). Deadpan shots can stay stills with slow push-ins; stillness is part of the joke.
- Chyrons, ticker, wrestling VS card, globe graphic, end card: my own motion graphics (Playwright canvas or
  PIL), driven by the song timing.
- Shots cut on bar lines; ~45-55 shots.

Chyron and ticker copy bank (use, cut, add):
- BREAKING: HORSE LOOSE IN HOSPITAL
- EXPERT: MAN WHO ONCE SAW BIRD IN AIRPORT
- UPDATE: HORSE USED ELEVATOR
- HORSE CALM, SOURCES SAY
- DEVELOPING: HORSE NO LONGER CALM
- HIPPO CLAIMS LAKE OF MUD MAKES HIM "CRAZY"
- HORSE FIRES HORSE-CATCHER
- BRUNCH ATTENDEES: "THERE SHOULDN'T BE A HORSE IN THE HOSPITAL"
- JUST 'CAUSE YOU'RE ACCURATE DOESN'T MEAN YOU'RE INTERESTING
- HOSPITAL WAS "INEFFICIENT," SAYS MAN WHO OPENED DOOR
- HORSE "AS CONFUSED AS YOU ARE"
- DAY 1,4xx OF HORSE

## Quality bar (check before calling it done)

- Would a stranger laugh at least five times on first watch, without knowing the bit?
- Does every chyron earn its frames, or is it noise over a joke that already landed?
- Is the horse the same horse in every shot?
- Can you understand every sung word on a phone speaker?
- Does the mirror beat (bridge) land in one image, without a speech?
- Contact sheet reviewed, then a 30 s slice, then the full render.

## Handoff checklist (for the build session)

1. Keys in the environment: `ELEVENLABS_API_KEY`, `GEMINI_API_KEY`, and a video key (`FAL_KEY`, or
   `HIGGSFIELD_API_KEY` if that's what Joel uses). Never print values.
2. Suno keeper from Joel (Google Drive file, or committed to this branch) -> `songvid suno horse-hospital --import`.
3. Spend cap on paid video generation: whatever Joel approved in chat. Track spend in `spend.md` and stop at the cap.

## Change log

- 2026-10-06: concept, song, and this bible written. Waiting on the Suno keeper and keys.
