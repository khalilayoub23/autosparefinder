# Learned-keyword backlog review (read-only evidence check)

Generated 2026-10-20. Source: `category_learned_keywords`, tokens awaiting the owner (top-voted row `pending`, observations >= 3, agreement >= 0.8). Each token was run through `category_learning.evidence_profile(db, token, proposed=category)` in a READ ONLY process (`isolate_process()`). **Nothing was approved, rejected, applied or written.** Per-token data: `docs/fitment/keyword_backlog_review.jsonl`.

Runtime: about 30 min of DB work in two segments (17:23-17:31 UTC, 289 tokens; stopped for the backend restart; 17:35-17:58 UTC, 2,236 tokens, 1,379 s). 4,486 read-only transactions, 0 Redis calls. Pauses for DB load: 0 (load checked before each segment and every 200 tokens: 0 long queries, 0 lock waiters, no run_all_tasks running).

## Counts per verdict

| verdict | tokens |
|---|---|
| category_mismatch | 1260 |
| insufficient_evidence | 558 |
| ok | 480 |
| ambiguous | 226 |
| error | 1 |
| **total** | **2525** |

Meaning: `ok` = proposed category is the top category among already-classified parts containing the token and beats the runner-up by >= 1.8x (what `approve()` requires). `category_mismatch` = evidence says another category. `ambiguous` = proposed is top but margin < 1.8x. `insufficient_evidence` = fewer than 12 classified parts contain it (approve() refuses without force). `blocked` = on the never-learn blocklist (pending_for_owner hides these).

Caveat: evidence is a substring match (`ILIKE %token%`) on name/name_he, the same as the live gate; short tokens also match inside longer words. `ok` means the gate would pass, not that a human checked every match. VERIFIED only to that extent.

## Ready to approve: verdict `ok` (480)

**Review before bulk-approving.** 147 of the 480 `ok` tokens are 4 characters or shorter, and several are word fragments (`fil`, `trans`, `transm`, `inte`, `brak`, `קונ`, `gtmo`). The gate matches `ILIKE %token%`, so a fragment also hits unrelated words: `inte` matches "interior" and "intercooler", `fil` matches "filter" and "profile". An `ok` verdict on a fragment means only that its classified evidence happens to be dominated by one category. ASSUMED safe, not verified by a human. Approving full words first is the lower-risk order.

The catch-all match counts below overlap (one part can contain several tokens). Their sum, 25,746, is an upper bound, not a number of distinct parts.

Sorted by `catch-all matches` = active parts in `כללי` whose name/name_he contains the token (capped at 200,000). This is an UPPER BOUND on what the bulk apply would move: the matcher is longest-keyword-wins, so some of those parts would land in another category via a longer rule.

| token | proposed | catch-all matches | obs | agreement | margin | share | example classified parts |
|---|---|---|---|---|---|---|---|
| computer | air-conditioning-heating | 1618 | 43236 | 0.88 | 2.06 | 0.473 | Add Tripcomputer K; Bracket Computer In; Bracket Computer Pa |
| damp | suspension-steering | 1238 | 3 | 1.0 | 19.47 | 0.838 | 780 Top strut mount bearing OPTIMAL F8-6267; ACKOJA A53-1530 KIA Sedona I (GQ) shock absorber; Audi A5 B9 Sportback Top strut mount MEYLE 100 412 2045 |
| trans | gearbox | 1166 | 3379 | 0.858 | 5.79 | 0.547 | ABAKUS 053-018-0003 Intercooler Aluminium, Plastic, Manual T; ABAKUS 100-00-157 Automatic transmission filter kit with gas; ABAKUS 135-02-004 FORD Orion Mk3 (GAL) headlight switch |
| adapter | electrical | 937 | 6695 | 0.91 | 2.68 | 0.383 | A.B.S. 96053 Adapter, brake lines; Adapter; ADAPTER-30647008 |
| buckle | safety-systems | 924 | 36169 | 0.873 | 2.17 | 0.494 | 2nd Seat Buckle Lh; 2th Seat Buckle Lh; 3rd Seat Buckle Lh |
| fil | filters | 779 | 5 | 1.0 | 6.41 | 0.747 | 65mm 14 Flute Filter Wrench; 9-3SS 03′-11′ 4 cyl Petrol models (B207) – Genuine Saab Oil ; Air Filter And Housing |
| transm | gearbox | 667 | 4 | 0.8 | 7.21 | 0.671 | 9-5 2002 to 2010 all automatic transmission models – Drivesh; ABAKUS 009-017-0016 Engine radiator Aluminium, Manual Transm; ABAKUS 141-04-141 CV joint kit with grease lubrication, Manu |
| gate | body-exterior | 606 | 15568 | 0.908 | 5.13 | 0.591 | ABAKUS 101-00-069 FORD Freestyle Mk3 Hatchback tailgate stru; ABAKUS 101-00-376 Tailgate strut Extention Force: 445N; ABAKUS 132-038-027 Tailgate release switch |
| lift | body-exterior | 557 | 15523 | 0.813 | 2.43 | 0.417 | 6 Inch Lift Kit Long Arm & FOX 2.5 Coil-Over Conversion Dodg; ACAUTO AC-05DN20 Ford S-Max Mk1 ac compressor clutch; AJUSA 16097100 FORD Tourneo Connect / Grand Tourneo Connect  |
| inte | interior-comfort | 534 | 4 | 1.0 | 1.83 | 0.261 | 11 Rear Integr; 12 Rear Integr; 14 Rear Integr |
| entr | electrical | 492 | 4 | 1.0 | 2.11 | 0.26 | 4MAX 0219-01-00544P Catalytic converter Centre, Lower, with ; 4MAX 0219-06-0186P Exhaust pipe gasket Rear, Centre, Front; 4MAX 0219-14-0016P Exhaust System Centre |
| exte | body-exterior | 457 | 5 | 1.0 | 2.39 | 0.505 | 1 2 Impact Extension; ABAKUS 053-32-861 Side indicator Left Exterior Mirror; ABAKUS 054-09-861 Side indicator Left Exterior Mirror |
| filler | fuel-air | 445 | 4857 | 0.88 | 2.1 | 0.395 | 3RG 80454 Oil filler cap; 3RG 80687 Oil filler cap; 3RG 81217 Oil filler cap |
| tension | belts-chains | 436 | 1253 | 0.985 | 5.6 | 0.741 | 1 2 Impact Extension; 3RG 13242 Tensioner Lever, v-ribbed belt; 3RG 13702 Tensioner Lever, v-ribbed belt |
| ecm | electrical | 420 | 279 | 0.889 | 5.58 | 0.786 | Becm Chem P30; Ecm; ECM |
| arm | suspension-steering | 374 | 3339 | 0.808 | 1.94 | 0.56 | 9-3 98′-02′ & NG900 94′-98′ Rotor arm – Genuine Saab; 9-5 98′-10′ all models – Rear Upper Cross Stay/Control Arm; ABAKUS 103-00-224 Windscreen wiper arm Rear, without wiper b |
| protect | body-exterior | 348 | 1108 | 0.925 | 7.66 | 0.695 | Absorber Rollover Protection 519902; AGC 1257109 Trim- / Protection Strip, windscreen; ALKAR 4901444 Trim / protective strip, radiator grille Left |
| disc | brakes | 333 | 604 | 0.895 | 26.39 | 0.866 | ABAKUS 131-07-133 Brake disc backing plate Front Axle Left; ABAKUS 131-07-134 Brake disc backing plate Front Axle Right; ABAKUS 131-07-610 Skoda Roomster 5j brake disc back plate |
| cup | interior-comfort | 314 | 4182 | 0.976 | 3.52 | 0.624 | 245 35r20 Mi Pilot Spt Cup; ABAKUS 135-99-018 Cup holder Rear, Centre; AMiO 02492 Cup holder Radiator Grille |
| shoe | brakes | 308 | 11099 | 0.995 | 68.06 | 0.944 | 030137-90882 BRAKE DRUM SHOES FITTING KIT REAR ATE FOR DACIA; 06040 BRAKE SHOE SET KIT REAR LPR FOR ROVER 400,200,45,25,ST; ABAKUS 231-05-008 Handbrake shoes Rear Axle, without lever |
| פלסטיק | body-exterior | 290 | 4654 | 0.97 | 4.3 | 0.634 | BME SEHCA כסוי פלסטיק; CO DE EUOJ פלסטיק דופן; COVER |
| sen | electrical | 288 | 132 | 0.868 | 5.58 | 0.505 | ABAKUS 053-026-011 Coolant expansion tank with coolant level; ABAKUS 120-02-100 Peugeot RCZ Coupe abs sensor; ABAKUS 120-05-048 Camshaft position sensor Hall Sensor |
| cooler | cooling | 234 | 238 | 0.869 | 3.94 | 0.539 | ABAKUS 042-018-0002 Intercooler Aluminium, Manual Transmissi; ABAKUS 051-018-0003 Intercooler Aluminium, Plastic, Manual T; ABAKUS 053-018-0009 Intercooler Aluminium, Plastic, Manual T |
| sun | interior-comfort | 226 | 5257 | 0.946 | 2.55 | 0.592 | Altima 13 Sunshield Silvr; BERU Z8 NISSAN Silvia (S110) spark plug; Bracket Sun Roof |
| חשמל | electrical | 217 | 396 | 0.99 | 2.95 | 0.447 | Actuator 111033; AHC RUETO מנוע חשמלי; BUMPER ASY - FRONT |
| visor | interior-comfort | 207 | 656 | 0.928 | 45.92 | 0.948 | Door Visors; ILLUMINATED VISOR; Instrument Cluster Visor |
| למראה | body-exterior | 203 | 3806 | 0.823 | 10.47 | 0.771 | Additional tode; ALP RIORIM זכוכית למראה; Auxiliary tuode |
| זכוכית | body-exterior | 203 | 3522 | 0.879 | 10.67 | 0.832 | (2007) זכוכית לוח שעונים; AKK414-6 זכוכית מראה ימין; ALP RIORIM זכוכית למראה |
| stee | suspension-steering | 180 | 6 | 0.857 | 16.16 | 0.829 | 9-3SS 07′-11′ all RHD 1.8T 2.0T (B207) Power Steering Rack; ABAKUS 134-01-002 Clock spring for steering wheel; ABAKUS 135-04-006 Steering column switch |
| mission | gearbox | 178 | 630 | 0.971 | 6.8 | 0.601 | 9-5 2002 to 2010 all automatic transmission models – Drivesh; ABAKUS 141-04-141 CV joint kit with grease lubrication, Manu; ABS Sensor fits ROVER 416 RT, XW 1.6 Front Left 95 to 99 Man |
| mod | electrical | 172 | 53 | 0.964 | 9.34 | 0.717 | 9000 85′-89′ all models – Front Right Hand ABS Sensor; 9000 85′-89′ all models with ABS Outer CV Joint Kit; 9-3 98′-00′ all T5 models |
| zebra | air-conditioning-heating | 165 | 609 | 0.807 | 4.3 | 0.555 | Panel Assembly Console Yr357l Zebra Wood; Panel L Front Switch Yr357l Zebra Wood; Panel L Rear Switch Yr357l Zebra Wood |
| glove | interior-comfort | 164 | 747 | 0.97 | 5.45 | 0.76 | Bin-glovebox; Bmw Group Mechanics Gloves 833520; Bo Glovenh89L |
| start | electrical | 160 | 2367 | 0.976 | 20.26 | 0.916 | AISIN ESMMB-7001 Starter motor Starter Output: 2kW, Voltage:; AISIN ESMMB-7007 Starter motor Starter Output: 1,7kW, Voltag; AISIN ESMVG-7017 Starter motor Starter Output: 1,1kW, Voltag |
| speed | electrical | 160 | 472 | 0.808 | 2.31 | 0.375 | 3rd Speed Gear; ABAKUS 010-017-0002 Engine radiator Aluminium, 5-Speed Manua; A.B.S. 37823 PORSCHE 911 Speedster (997) brake pads |
| cam | engine | 150 | 56 | 0.836 | 2.71 | 0.578 | ABAKUS 120-05-024 Mercedes E Class W124 camshaft sensor; ACKOJA A32-0021 Camshaft Adjuster Intake Side; AE CAM1007 Skoda Octavia 3 camshaft |
| silve | body-exterior | 149 | 3 | 1.0 | 1.86 | 0.311 | 18in 6 Double Spoke Sparkling Silver; 19in Silver Alloy Wheel; 1 Set Complete Summer Wheels Silver |
| neck | fuel-air | 148 | 4517 | 0.988 | 12.68 | 0.785 | Abarth Pullover V-Neck Men XL; Bracket Filler Neck; Bracket F Neck |
| reading | lighting | 145 | 4766 | 0.995 | 34.27 | 0.902 | 9-3 Sports 2003 on – Lucas Rear Reading Light Bulb 12V 4W; Baseus CRYDD01-02 Reading Light; Baseus DGXW-01 Reading Light |
| uniside | body-exterior | 145 | 2069 | 0.968 | 3.91 | 0.648 | Inner Uniside; Inner Uniside Anchor Plate; Inner Uniside Bracket |
| vapor | air-conditioning-heating | 139 | 904 | 0.964 | 2.61 | 0.634 | A C Evaporator; A/C Evaporator Cleaner; A C Evaporator Core |
| shoulder | safety-systems | 133 | 4301 | 0.974 | 3.19 | 0.699 | Cover Shoulder Belt; hanger fr shoulder; Land Rover Lap And Shoulder |
| finish | body-exterior | 132 | 1183 | 0.98 | 80.09 | 0.951 | Alternator Assembly, Black Chrome Finish, 90 amp; A-Post Closing Finisher LH; Bonnet Air Vent Finisher, Left Hand |
| saloon | suspension-steering | 130 | 370 | 0.927 | 1.97 | 0.23 | ABAKUS 003-017-0030 VW Polo 5 Saloon engine radiator; ABAKUS 123-00-027 VW Passat 3bg Saloon cylinder head; ABAKUS 132-004-150 VW Passat B7 Saloon central locking syste |
| deco | body-exterior | 128 | 65 | 0.97 | 2.29 | 0.437 | Aluminum Decor Strip Front 514025; Aluminum Decor Strip Front C000090; Aluminum Decor Strip Rear L 514025 |
| כיסא | interior-comfort | 120 | 1042 | 0.996 | 2.95 | 0.507 | 01 ריפוד מושב כיסא קד'ימ; Air Deflector; COVER ASY - REAR SEAT HEA |
| blade | wipers-washers | 120 | 555 | 0.816 | 26.16 | 0.839 | 26 Beam Wiper Blade; 780 Wiper blade RIDEX 298W0152; ABAKUS 103-00-059-P Wiper arm set with cap, with integrated  |
| בטחון | safety-systems | 116 | 5502 | 0.942 | 6.19 | 0.584 | 1.75 'טבעת בטחון לציר אח; 2.00 'טבעת בטחון לציר אח; BELT'A'(R),REAR SEAT |
| lap | safety-systems | 116 | 4532 | 0.982 | 2.92 | 0.527 | 3 Mini Flap Di; Actuator Drive Exhaust Flap 189900; Air flaps, uode |
| sync | gearbox | 108 | 266 | 0.996 | 9.8 | 0.765 | 2nd Speed Synchronising Sleeve; 3RG 23765 Synchronizer Ring, manual transmission; AISIN MTPT-00063 Synchronizer Ring, manual transmission |
| under | body-exterior | 107 | 138 | 0.92 | 9.71 | 0.796 | 3M RUBBERIZED UNDERCOATING; AERO UNDER SHIELD ASM - FRT SUBFRAME; Bar Under Guard Fr |
| reclin | interior-comfort | 105 | 4 | 1.0 | 3.04 | 0.665 | adjuster l reclining; Arm Sub Assy Reclin; Base Reclining Remo |
| oyo | suspension-steering | 104 | 25 | 0.962 | 2.42 | 0.262 | A.B.S. 16031 Toyota MR2 AW11 brake discs; A.B.S. 16613 Toyota Land Cruiser 80 brake discs; A.B.S. 16932 TOYOTA Rush II Off-Road (F700) brake discs |
| feed | fuel-air | 102 | 896 | 0.987 | 7.37 | 0.635 | 3RG 80404 Holding Bracket, fuel feed pump; ACKOJA A52-09-0016 Fuel feed unit Petrol, Electric; ACKOJA A52-09-0028 Fuel feed unit Electric |
| אקלים | air-conditioning-heating | 101 | 70 | 1.0 | 6.7 | 0.684 | CONTROL; E64 פנל בקרת אקלים; # G12 פנל בקרת אקלים |
| synch | gearbox | 100 | 48 | 0.889 | 10.47 | 0.806 | 2nd Speed Synchronising Sleeve; 3RG 23765 Synchronizer Ring, manual transmission; AISIN MTPT-00063 Synchronizer Ring, manual transmission |
| protective | body-exterior | 98 | 1134 | 0.803 | 3.34 | 0.517 | 3m Overspray Protective Shee 751010; Aesop Protective Body Lotion Spf50 150ml; ALKAR 4901444 Trim / protective strip, radiator grille Left |
| cooling | cooling | 91 | 1302 | 0.999 | 2.39 | 0.502 | 3000GT Brake Fluid A.B.S. 7502; ABAKUS 011-017-0011 Engine radiator Aluminium, Mechanically ; AIC 53105 Cooling fan relay |
| pullover | merchandise | 90 | 49 | 0.86 | 5.58 | 0.637 | Abarth Pullover V-Neck Men XL; Champion Pullover Unise; Champion Pullover Unisx |
| קונ | interior-comfort | 89 | 346 | 0.874 | 2.93 | 0.462 | ALTERNATOR ASSY; Armrest; ARMREST ASY - CONSOLE |
| catalytic | exhaust | 88 | 49 | 1.0 | 10.57 | 0.911 | 4MAX 0219-01-00544P Catalytic converter Centre, Lower, with ; 9-5 ’98-03 3.0 V6 Petrol Models(B308E) – Catalytic Converter; AJUSA 01490700 FIAT E-Scudo Minibus catalytic converter |
| synchr | gearbox | 85 | 108 | 1.0 | 11.39 | 0.826 | 2nd Speed Synchronising Sleeve; 3RG 23765 Synchronizer Ring, manual transmission; AISIN MTPT-00063 Synchronizer Ring, manual transmission |
| scorpion | interior-comfort | 84 | 505 | 0.886 | 1.86 | 0.457 | 235 55r20 Pi Scorpion Ms Xl; 255 45r21 Pi Scorpion Ms Xl; 255 55 R18 109h Scorpion Ice 369042 |
| leaf | suspension-steering | 83 | 299 | 0.984 | 17.73 | 0.837 | 11 Leaf Uv Shield; 2x DELPHI BG3765C Brake Discs Rear For Infiniti G Nissan Juk; 54440 Brake Disc x2 Rear Fits Infiniti G Coupe Saloon Nissan |
| pirelli | wheels-bearings | 83 | 81 | 0.988 | 7.24 | 0.84 | 225 35 R19 88y Xl Pirelli Pz 369041; 225 40 R19 89w Pirelli P Zer 369041; 225 45 R17 91y Pirelli P6000 369041 |
| synchro | gearbox | 81 | 107 | 1.0 | 11.25 | 0.826 | 2nd Speed Synchronising Sleeve; 3RG 23765 Synchronizer Ring, manual transmission; AISIN MTPT-00063 Synchronizer Ring, manual transmission |
| booster | brakes | 81 | 94 | 0.839 | 16.78 | 0.851 | 2007 CHRYSLER PT CRUISER BRAKE BOOSTER PETROL LHD WITH CYLIN; 9-3SS 06′-10′ all 2.8 V6 models (B284) -Brake Booster Pipe R; AISIN BBTS-025 Brake Booster without brake master cylinder |
| movable | body-exterior | 78 | 594 | 0.912 | 5.49 | 0.773 | Ashtray Removable; bolt assy removable; Bolt Assy Removable |
| equal | body-exterior | 78 | 24 | 1.0 | 5.8 | 0.692 | Bracket Equalizer S; Equalizer; EQUALIZER |
| handheld | gearbox | 74 | 3428 | 0.988 | 11.37 | 0.828 | 3d Tire Tread Handheld 369041; Handheld Transmitter; Handheld Transmitter Key Cover Lacquered |
| multiplex | electrical | 74 | 56 | 0.966 | 42.0 | 0.913 | computer multiplex; Computer Multiplex; Cover Multiplex Net |
| michelin | wheels-bearings | 73 | 257 | 0.996 | 6.68 | 0.761 | 225/45R17 MICHELIN PRIMACY 3 91w; 275 35 Z R18 Michelin 369041; 275/45R20 Michelin V |
| chassis | suspension-steering | 73 | 104 | 0.852 | 2.54 | 0.285 | 9-3SS 03′-12′ CV – Lowered Sports Chassis Aero Spring Kit (4; A.B.S. 17732 MERCEDES-BENZ Sprinter 3-T Platform/Chassis (W9; A.B.S. 220450 OPEL Movano C Van (U9) suspension ball joint |
| woofer | audio-electronics | 72 | 57 | 0.803 | 3.79 | 0.553 | 10inch Powered Sub-Woofer 4-Dr - Exc Navigation; 10inch Powered Sub-Woofer - 5-Door - W/ Navigation; 10inch Powered Sub-Woofer - 5-Door - W/O Navigation |
| shifter | gearbox | 71 | 1672 | 0.992 | 25.65 | 0.877 | 3RG 25330 FORD Topaz II Saloon gear shifter; Automatic shifter assembly; Automatic Transmission Shifter |
| principle | merchandise | 71 | 47 | 0.87 | 3.12 | 0.617 | Porsche Principle 2022 Book Only Marke; Porsche Principle 2022 Book Only Nedc; Porsche Principle 2022 Book Only Wltp |
| sus | suspension-steering | 69 | 15 | 1.0 | 16.59 | 0.913 | A.B.S. 210430 Suspension arm Front Axle Right, outer, Lower,; A.B.S. 212158 Suspension arm Rear Axle Left, Rear Axle Right; A.B.S. 220231 Renault Laguna 1 suspension ball joint |
| sweat | merchandise | 68 | 16 | 0.941 | 4.7 | 0.718 | Ahead Unisex Sweater Dark Grey; Bbmw Mms Sweat Pants Reg Cc 999999; Bmw M Motorsport Sweat Jacke 9917800001 |
| compres | air-conditioning-heating | 67 | 6 | 1.0 | 3.76 | 0.694 | ACAUTO AC-02HA01 AC compressor control valve; ACAUTO AC-02SD04 AC compressor control valve; ACAUTO AC-02SD11 AC compressor control valve |
| לכיסא | interior-comfort | 66 | 450 | 0.959 | 2.03 | 0.43 | Air Deflector; ANCHOR; AR EXA בורג לכיסא |
| מרים | engine | 66 | 55 | 0.932 | 8.3 | 0.779 | )3.32)מרים שסתום; ADJUSTER ASY; ADJUSTER,HYDRAULIC L |
| freeze | fluids | 66 | 24 | 0.96 | 34.84 | 0.939 | 33 10 1133 SWAG Antifreeze; 5 Litres of AntiFreeze; 5 Litres of Comma Super Longlife AntiFreeze |
| mold | body-exterior | 66 | 16 | 0.8 | 18.92 | 0.901 | Back Glass Reveal Molding; Belt Molding; Belt Molding Clip |
| מנעולים | body-exterior | 65 | 1113 | 0.966 | 11.24 | 0.841 | 4DR סט מנעולים לרכב; Door Latch Service K; HYUNDAI 81900K2C00 |
| drum | brakes | 62 | 429 | 0.958 | 23.8 | 0.885 | 04-0871 DRUM WHEEL BRAKE CYLINDER REAR METELLI FOR LAND ROVE; 2565 DRUM WHEEL BRAKE CYLINDER REAR RIGHT ABS FOR ROVER 200 ; 2736 DRUM WHEEL BRAKE CYLINDER REAR RIGHT LEFT ABS FOR LAND  |
| מהירות | electrical | 60 | 324 | 0.95 | 6.09 | 0.648 | 12 NLS נגד מהירות למזגן דימקס מ; 12 נגד מהירות למזגן דימקס מ; 21 נגד מהירות למזגן דימקס מ |
| glovebox | interior-comfort | 59 | 471 | 0.992 | 3.24 | 0.748 | Bin-glovebox; Bin-Glovebox Door; Cover, Glovebox |
| טלטל | wheels-bearings | 57 | 143 | 0.851 | 2.92 | 0.619 | #1 מיסב טלטל; #2 מיסב טלטל; #3 מיסב טלטל |
| iii | suspension-steering | 55 | 105 | 0.847 | 2.01 | 0.227 | 1 - Land Rover Series II/III 88&quot; Full Vehicle Premium C; A.B.S. 17590 OPEL Frontera III (OV24) brake discs; A.B.S. 36011 MG Midget MkIII brake pads |
| germany | suspension-steering | 55 | 82 | 0.872 | 4.0 | 0.492 | at autoteile germany at10110 Transmission control module (TC; at autoteile germany at10341 Accelerator Pedal; at autoteile germany at10404 Crankshaft sensor with screw |
| undertray | body-exterior | 55 | 40 | 1.0 | 30.14 | 0.963 | Centre Undertray; Engine Undertray; Front Bumper Undertray |
| fixed | body-exterior | 53 | 722 | 0.968 | 7.82 | 0.744 | Avalon Fixed; BOARD FIXED; "BRACKET, Fixed Glass Retention" |
| gtmo | fluids | 51 | 2477 | 0.999 | 11.57 | 0.88 | gtmo 0w16 bk; Gtmo 0w16 Bk; gtmo 0w16 bk sp |
| corner | body-exterior | 51 | 678 | 0.99 | 8.19 | 0.74 | 900 79′-86′ Clear Corner Lenses (Pair); 9-3 Sports 03′-07′ all models – Euro Style Bumper Corner Fro; Air Dam, Rear, Corner, Grey |
| דיסק | brakes | 51 | 8 | 0.8 | 4.25 | 0.522 | # '15 רדיו דיסק אקס; 1700R רדיו דיסק סט' פיונר; 1.9 דיסק מצמד |
| ladies | merchandise | 49 | 167 | 0.928 | 2.82 | 0.603 | Belt Ladies; Belt Ladies Black; Cap Ladies |
| player | audio-electronics | 48 | 148 | 0.876 | 2.66 | 0.481 | audio cassette player; bracket disc player; Cassette Player |
| lambswoo | interior-comfort | 48 | 35 | 0.921 | 2.2 | 0.621 | Lambswool Ru; Lambswool Sc; LHD Mocha Lambswool Rug Set |
| estate | suspension-steering | 47 | 139 | 0.903 | 2.07 | 0.229 | 323 I Estate V-Belt TRUCKTEC AUTOMOTIVE 04.19.061; ABAKUS 017-025-0010 Ford Mondeo Mk5 Estate engine thermostat; ABAKUS 141-04-143 Fiat 500 L cv joint |
| bcm | electrical | 47 | 36 | 1.0 | 2.93 | 0.651 | 2 BCM מחשב; Bcm; BCM |
| pearl | body-exterior | 47 | 35 | 0.814 | 5.16 | 0.654 | adapter assembly fuel filler lid r543p carnelian red pearl; Antenna Assembly Radio And Telematics And Gps And Xm R548p B; Antenna Assembly Radio And Xm R548p Basque Red Pearl Ii |
| brak | brakes | 46 | 1790 | 1.0 | 34.78 | 0.899 | Spring Brake Pedal; Ford Transit Custom Minibus Brake disc BREMBO 08.C242.20; Disc Brake Pad Set Front |
| turanza | wheels-bearings | 46 | 357 | 0.864 | 7.77 | 0.759 | 205 55 R16 91w Turanza Er300 369041; 225 50 R17 94v Turanza El400 369041; 245 40r19 94y Turanza Er300a 369041 |
| בטיחות | safety-systems | 45 | 26 | 0.839 | 33.59 | 0.875 | 1ST ROW RETRACTOR ASSEMBLY LH; ADJUSTER,SHOULDER; BELT'A'(L),REAR SEAT |
| conso | interior-comfort | 44 | 10 | 0.833 | 10.4 | 0.806 | armrest assembly console nh585l mono gray; armrest assembly console yr487l straight orange; armrest assembly l console nh900l deep black |
| מגנזיום | wheels-bearings | 42 | 613 | 0.975 | 78.78 | 0.95 | 17X7 גלגל מגנזיום; 18X7 גלגל מגנזיום; 18X7 חישוק גלגל מגנזיום |
| nike | merchandise | 42 | 18 | 0.9 | 21.6 | 0.956 | Duffel By Nike, Black; Nike; Nike Dri-Fit 1/2 Zip, Bl |
| steer | suspension-steering | 42 | 9 | 1.0 | 43.77 | 0.913 | 9-3SS 07′-11′ all RHD 1.8T 2.0T (B207) Power Steering Rack; ABAKUS 134-01-002 Clock spring for steering wheel; ABAKUS 134-01-006 Clock spring for steering wheel |
| בידוד | body-exterior | 41 | 434 | 0.828 | 2.58 | 0.495 | Fabric-Tape ode; Front-hood sound insulating pad; Hood Insulation Pad |
| שליטה | electrical | 41 | 89 | 0.957 | 4.12 | 0.491 | BODY AY- CONTROL VLV; BOLT; CABLE ASSY |
| citro | suspension-steering | 41 | 59 | 0.937 | 2.08 | 0.229 | AE CAM1027 CITROËN Relay II Platform / Chassis (250) camshaf; AJUSA 16097100 CITROËN C5 X III fuel pump; AKS DASIS 065031N CITROËN C4 Spacetourer Box Body / Estate ( |
| susp | suspension-steering | 41 | 8 | 1.0 | 16.69 | 0.914 | A.B.S. 210430 Suspension arm Front Axle Right, outer, Lower,; A.B.S. 212158 Suspension arm Rear Axle Left, Rear Axle Right; A.B.S. 220231 Renault Laguna 1 suspension ball joint |
| safety | safety-systems | 40 | 274 | 0.972 | 9.51 | 0.756 | AUTOMEGA 211191510 Wiper Linkage for left-hand drive vehicle; BLIC 6010-14-054424P Safety Hook, bonnet lock; Bonnet Safety Catch |
| לתיבת | gearbox | 39 | 347 | 0.887 | 14.99 | 0.853 | * 10-בורג לתיבת הילוכים מ"מ; 13901; 4X2 סט אטמים לתיבת מהלכים ידנית |
| מיתרים | body-exterior | 39 | 179 | 0.865 | 2.5 | 0.417 | אום 02M ניילוק למתיחת רשת מיתרים; רשת מיתרים; רשת מיתרים 4X8 מרווח 5 מ" מ עובי מיתר 8.1 מ" מ |
| goodyear | wheels-bearings | 39 | 29 | 1.0 | 21.75 | 0.917 | 205 55 R16 91h Goodyear Eagl 369041; 225 50 R17 94h Goodyear Eagl 369041; 245 40 R19 94y Goodyear Exce 369041 |
| sneakers | merchandise | 37 | 27 | 0.964 | 2.13 | 0.508 | 25y Carrera Gt Sneakers Black; 25y Carrera Gt Sneakers Silver; 911 Targa 60y Sneakers Beige |
| synchroniz | gearbox | 36 | 1097 | 0.994 | 32.32 | 0.915 | 3RG 23764 Synchronizer Ring, manual transmission; 3RG 23765 Synchronizer Ring, manual transmission; AISIN MTPT-00063 Synchronizer Ring, manual transmission |
| surround | body-exterior | 36 | 933 | 0.976 | 21.61 | 0.849 | 7" Chrome Surrounds. Plastic, Pair.; 9-3 98′-02′ & NG900 94′-98′ RH plas mirror surround (PAINTAB; 9-3 98′-02′ & NG900 94′-98′ RH plastic door mirror surround |
| dunlop | wheels-bearings | 36 | 22 | 0.957 | 26.08 | 0.927 | 225 55 R17 97h Dunlop Sp Win 369042; 235 45 R17 94y Dunlop Sport 369041; 245 35 R20 95y Dunlop Sport 369041 |
| shoes | brakes | 35 | 490 | 0.946 | 121.84 | 0.97 | 1 Set Brake Shoes With Linings; 204 Coupe Brake Shoe Set RIDEX 70B0002; ABAKUS 231-05-032 Handbrake shoes Rear Axle |
| negative | electrical | 35 | 26 | 1.0 | 1.99 | 0.582 | Battery Cable Negative 611030; Battery Cable Negative A611a0; Battery Cable Negative For 611030 |
| cotter | fasteners | 34 | 990 | 0.977 | 4.55 | 0.663 | Adjust Cap Cotter Pin; Apron Rail Assembly Cotter Pin; Axle Assembly Cotter Pin |
| unisex | merchandise | 33 | 25 | 0.862 | 2.59 | 0.562 | Ahead Unisex Shirt Grey; Ahead Unisex Sweater Dark Grey; Ahead Unisex T Shirt White |
| exh | exhaust | 32 | 25 | 0.926 | 18.29 | 0.856 | 3RG 70609 Exhaust hanger; 3RG 71603 Exhaust pipe gasket; 4MAX 0219-01-04806P Exhaust Pipe Front |
| prom | electrical | 32 | 10 | 0.909 | 4.25 | 0.425 | 2k Advanced Dry Promoter 759801; 3m Wheel Weight Promotion Kit; 4LT שמן מנוע 5W40 C3 GAZPROMNEFT |
| catalt | exhaust | 31 | 854 | 0.996 | 6.55 | 0.862 | Cataltanddnppe; Catalt&Dnpp; Catalt&Dnppe |
| dnppe | exhaust | 31 | 853 | 0.996 | 6.45 | 0.861 | Cataltanddnppe; Catalt&Dnppe; Catalt&Dnppe-Exhaust |
| מגיני | fuel-air | 31 | 459 | 0.864 | 4.75 | 0.777 | COVER ASY; Cylinder-heaode; F/650 ער' מגיני ידיים |
| spoke | wheels-bearings | 31 | 22 | 0.917 | 7.53 | 0.725 | 15 - Moto Lita Mk3 Dished 15&quot; Wood Rim This Slot Style ; 17 Crux 7 Twin Spoke; 17 Lightweight 15 Spoke |
| נורת | lighting | 31 | 6 | 1.0 | 6.55 | 0.704 | Additional t; Bulb; BULB |
| flow | fuel-air | 30 | 25 | 0.833 | 2.96 | 0.565 | ABAKUS 120-08-068 Mass air flow sensor with housing; ABAKUS 120-08-106 Mass air flow sensor; AIC 74827 Fuel overflow hose suitable for biodiesel |
| זרם | electrical | 30 | 23 | 0.821 | 7.73 | 0.637 | 130A מחולל זרם; 145A מחולל זרם; 1.4 מחולל זרם |
| consol | interior-comfort | 30 | 21 | 0.955 | 15.15 | 0.838 | armrest assembly console nh585l mono gray; armrest assembly console yr487l straight orange; armrest assembly l console nh900l deep black |
| fascia | body-exterior | 30 | 19 | 0.905 | 2.11 | 0.463 | APPLIQUE FASCIA; APPLIQUE Fascia Right X8W MCD; APPLIQUE Fascia X8W MCD MCG |
| info | body-exterior | 29 | 1329 | 0.949 | 23.8 | 0.861 | ABAKUS 017-12-360 Bumper reinforcement Front; ABAKUS 042-49-370 Bumper reinforcement Rear; Air Bag Information Label |
| stiffener | body-exterior | 29 | 790 | 0.994 | 18.92 | 0.849 | Blade Assembly Stiffener; Bumper Cover Stiffener; DEIP.LBUOD פח למרכב |
| spark | engine | 29 | 621 | 0.981 | 52.21 | 0.906 | 18in 6 Double Spoke Sparkling Silver; 780 Spark plug CHAMPION CCH9808; A.B.S. 210517 Chevrolet Spark M 150 control arm |
| transmis | gearbox | 29 | 101 | 0.99 | 6.8 | 0.673 | 9-5 2002 to 2010 all automatic transmission models – Drivesh; ABAKUS 009-017-0016 Engine radiator Aluminium, Manual Transm; ABAKUS 141-04-141 CV joint kit with grease lubrication, Manu |
| handbag | merchandise | 29 | 13 | 0.812 | 1.86 | 0.448 | Handbag; HANDBAG 500 C MAXIPLURIBALL RED; HANDBAG 500 C MAXIPLURIBALL WHITE |
| קליפת | body-exterior | 28 | 18 | 0.947 | 8.33 | 0.862 | Front defroster COU קליפת דשבורד; PANEL ASY-ROOF; Roof Panel |
| heate | air-conditioning-heating | 28 | 5 | 1.0 | 2.22 | 0.435 | ABAKUS 037-015-0019-B Heater matrix; ABAKUS 038-015-0001 Heater matrix; ABAKUS 038-015-0014 Heater matrix |
| סטרט | electrical | 27 | 4 | 0.8 | 13.5 | 0.844 | 01 XEH SI בורג סטרטר; AGM 40 סטרטסטופ AH מצבר; BAGUE בוקסות לסטרטר |
| סיגריות | body-exterior | 26 | 111 | 0.874 | 3.42 | 0.636 | HYUNDAI 95120L1000; Lighter; LIGHTER, CIGAR |
| wip | wipers-washers | 26 | 22 | 1.0 | 17.64 | 0.867 | 26 Beam Wiper Blade; 780 Wiper blade RIDEX 298W0152; ABAKUS 103-00-224 Windscreen wiper arm Rear, without wiper b |
| wltp | merchandise | 26 | 20 | 0.8 | 2.62 | 0.538 | Catalogue Panamera G2ii Mym Wltp With; EXHAUST PIPE FRONT (EPF)without WLTP; Model Range Catalogue Mys Wltp |
| stabil | suspension-steering | 26 | 13 | 1.0 | 6.22 | 0.837 | Active Stabilizer Front 313530; Bar Stabilizer; BUSHNG-STABILZER |
| fend | body-exterior | 26 | 6 | 1.0 | 67.37 | 0.939 | 52933X DRUM WHEEL BRAKE CYLINDER REAR LEFT ABS FOR LAND ROVE; ABAKUS 004-33-201 Wing fender Left Front; ABAKUS 004-33-202 Wing fender Right Front |
| סולר | filters | 25 | 192 | 0.919 | 3.53 | 0.564 | 2017 מסנן סולר מ; 2021 1.9L מזרק סולר; 2.5/1.9 מסנן סולר |
| prep | brakes | 25 | 50 | 0.893 | 15.42 | 0.894 | 3m Wheel Surface Cleaner Prep 2000 16oz; ABAKUS 231-01-354 Brake pad set prepared for wear warning in; A.B.S. 36715 BMW Z8 E52 brake pads |
| tourer | suspension-steering | 24 | 46 | 0.979 | 2.32 | 0.244 | ABAKUS 042-017-0058-B Renault Laguna 2 Sport Tourer engine r; ABAKUS 141-01-156 MERCEDES-BENZ Marco Polo Camper (W447) whe; A.B.S. 201893 OPEL Insignia B Sports Tourer Box Body / Estat |
| hands | air-conditioning-heating | 24 | 24 | 0.828 | 3.84 | 0.575 | Attachment Clamp For Hands F C849900; Audio System, Bluetooth, Hands-Free, Mirage; Auxmic Handsfree Mic And Audio Cable |
| ethernet | electrical | 24 | 15 | 1.0 | 18.0 | 0.865 | bracket ethernet sw; Bracket Ethernet Sw; Cable For Ethernet 611015 |
| telep | audio-electronics | 23 | 119 | 0.983 | 2.71 | 0.483 | 9-3, 9-3SS & 9-5 with telephone; Adapter Telephone; ADAPTER,TELEPHONE |
| crank | engine | 23 | 100 | 0.87 | 8.95 | 0.768 | 3RG 10400 Crankshaft pulley 6PK, Ø: 128mm; ABAKUS 120-04-051 Crankshaft sensor; ABAKUS 120-04-062 Crankshaft sensor |
| bdy | body-exterior | 23 | 15 | 0.938 | 12.0 | 0.735 | 501005707ABDYJ; 501005708ABDYJ; 501007544ABDYJ |
| פלנטרי | gearbox | 23 | 8 | 0.8 | 3.0 | 0.522 | # 19 'פלנטרי בתיבת הילוכים אדג; CARRIER - OVERDRIVE PLANE; Carrier Planetary |
| gloves | interior-comfort | 22 | 296 | 0.946 | 3.4 | 0.727 | 1 Pair Assembly Gloves; Ammex Nitrile Gloves; Astro Grip 7 Mil Nitrile Gloves Size L |
| propeller | clutch-drivetrain | 22 | 276 | 0.975 | 1.83 | 0.442 | Bolt Fix Propeller Shaft; Bolt, Propeller Shaft; Boot Kit, Propeller Shaf |
| daco | suspension-steering | 22 | 33 | 1.0 | 3.12 | 0.635 | Audi A5 B8 Sportback Dust cover kit, shock absorber DACO Ger; DACO Germany 110202 Wheel bearing kit with integrated ABS se; DACO Germany 110205 Wheel bearing kit with integrated ABS se |
| occdec | body-exterior | 22 | 29 | 1.0 | inf | 1.0 | Occ Sport OCCDEC001 Front splitter; Occ Sport OCCDEC002 Front splitter |
| batte | electrical | 22 | 19 | 0.826 | 21.07 | 0.895 | ABAKUS 120-11-023 Tyre pressure sensor (TPMS) with battery, ; AGM Batterie; ALCA 509020 Battery Post Clamp |
| גוזון | wheels-bearings | 21 | 1127 | 0.878 | 2.57 | 0.319 | 1X01NOJUOG גוזון גיר; 1X8 NOJUOG גוזון מניפולד; 4/3NOLEMA גוזון מסנן שמן |
| outside | body-exterior | 21 | 695 | 0.983 | 47.13 | 0.941 | Alu Bolt Outside Torx With 889522; Bowden Cable Outside Door H 512060; Cap Door Outside Ha |
| רישוי | body-exterior | 21 | 469 | 0.971 | 5.39 | 0.739 | 2010 CRV תושבת לוחית רישוי; 25 'תושבת לוחית רישוי קד; 60 'תושבת לוחית רישוי אח |
| shade | interior-comfort | 21 | 246 | 0.965 | 3.11 | 0.641 | Airbag Unit Barrique Red Silver Shade; Airbag Unit Dark Night Blue Silver Shade; Ambient Light Silver Shade |
| transmissi | gearbox | 21 | 39 | 0.929 | 6.87 | 0.677 | 9-5 2002 to 2010 all automatic transmission models – Drivesh; ABAKUS 004-017-0046 Engine radiator Automatic Transmission; ABAKUS 009-017-0016 Engine radiator Aluminium, Manual Transm |
| mcb | electrical | 21 | 19 | 1.0 | 64.33 | 0.946 | FEBEST MCB-CW5 Propshaft centre bearing; MCB בקרה ל; MCB בקרת |
| kids | merchandise | 21 | 14 | 0.824 | 3.46 | 0.608 | Aor Kids Baseball Cap Cream; Aor Kids Hoodie Black; Aor Kids T Shirt Carbon |
| pulse | electrical | 21 | 11 | 0.917 | 1.91 | 0.373 | 1.953.609 EPS Sensor, crankshaft pulse; 1.953.742 EPS Sensor, crankshaft pulse; 2 Pin Pulse Gen Conn |
| fuse | electrical | 21 | 5 | 0.833 | 15.09 | 0.919 | Aero Kit Rear Diffuser; Aero Kit Rear Diffuser Brilliant Black; AMiO 03038 Fuse Box |
| suspen | suspension-steering | 21 | 5 | 1.0 | 16.55 | 0.915 | A.B.S. 210430 Suspension arm Front Axle Right, outer, Lower,; A.B.S. 212158 Suspension arm Rear Axle Left, Rear Axle Right; A.B.S. 220231 Renault Laguna 1 suspension ball joint |
| לדיבורית | audio-electronics | 20 | 1002 | 0.996 | 2.14 | 0.536 | טבעת נעילה לדיבורית Bluetotooth; יחידת בקרה לדיבורית; מיקרופון לדיבורית |
| timing | belts-chains | 20 | 818 | 0.99 | 2.24 | 0.66 | ABAKUS 142-01-509 Timing chain kit with chain tensioner, Sim; AE FOL240 Rocker Arm, engine timing Exhaust Side; AE TB268 Timing Belt Number of Teeth: 83 |
| synchroniser | gearbox | 20 | 384 | 1.0 | 4.0 | 0.8 | Carburettor Synchroniser; Hub Synchroniser 1; Hub Synchroniser 3 |
| mxm | wheels-bearings | 20 | 95 | 0.856 | 7.88 | 0.7 | 205 50 R17 93v Pilot Hx Mxm4 369041; 225 50 R17 94v Primacy Mxm4 369041; 225 55 R16 95v Pilot Hx Mxm4 369041 |
| corolla | suspension-steering | 20 | 53 | 0.946 | 1.98 | 0.24 | 03 corolla tire; ABAKUS 103-06-008 Toyota C-HR wiper motor; ABAKUS 112-1130L-LDEMN TOYOTA Corolla X Saloon (E150) headli |
| bsu | electrical | 20 | 21 | 0.913 | 11.2 | 0.767 | Hubsub Assy Mgntcltc; Zeekr Part 6608143305; Zeekr Part 8891243302BA0 |
| womens | merchandise | 20 | 16 | 0.941 | 2.29 | 0.565 | Aor Womens T Shirt White; Sportscar Together Womens Polo; Sportscar Together Womens Quarter Zip |
| diffuser | body-exterior | 20 | 12 | 0.857 | 9.57 | 0.713 | Aero Kit Rear Diffuser; Aero Kit Rear Diffuser Brilliant Black; Air Diffuser Rear Bumper |
| steeri | suspension-steering | 20 | 8 | 0.889 | 44.77 | 0.915 | 9-3SS 07′-11′ all RHD 1.8T 2.0T (B207) Power Steering Rack; ABAKUS 134-01-002 Clock spring for steering wheel; ABAKUS 134-01-006 Clock spring for steering wheel |
| fue | fuel-air | 20 | 6 | 1.0 | 3.13 | 0.683 | 3RG 82006 Renault Laguna 1 fuel pump; ACKOJA A70-11-0003 Fuel metering valve High Pressure Pump (l; ACKOJA A70-11-0008 Fuel metering valve High Pressure Pump (l |
| תפוחים | suspension-steering | 19 | 1171 | 0.998 | 17.14 | 0.857 | #4X4 מחבר למוט תפוחים אקו; OEM Part - מוט תפוחים שמאל; אום למוט תפוחים/תושמנוע |
| injection | fuel-air | 19 | 114 | 0.966 | 2.68 | 0.53 | 3RG 81291 Heat Shield, injection system; 3RG 81312 Heat Shield, injection system; 3RG 81313 Heat Shield, injection system |
| smj | suspension-steering | 19 | 27 | 1.0 | 3.0 | 0.714 | Chevrolet Aveo Saloon damping JAPKO SMJ0105; CHRYSLER Cirrus Saloon Top strut mount JAPKO SMJ0239; Chrysler Neon 2 Top strut mount JAPKO SMJ0101 |
| rectifier | electrical | 19 | 21 | 0.955 | 42.5 | 0.971 | Alternator Rectifier; Alternator Rectifier 25ACR; Alternator Rectifier for Range Rover Classic and Discovery 1 |
| אלקטרונית | electrical | 19 | 19 | 0.826 | 8.81 | 0.638 | A'D ELUDOM הצתה אלקטרונית; A'D ELUDO הצתה אלקטרונית; C GATEWAY ASSY |
| clut | clutch-drivetrain | 19 | 12 | 1.0 | 10.15 | 0.778 | 3RG 13649 Alternator clutch pulley Ø: 61mm, Width: 37,2mm; 3RG 22210 Release Fork, clutch; 3RG 22211 Release Fork, clutch |
| מנורת | lighting | 19 | 8 | 1.0 | 4.5 | 0.676 | Back Up Light Assembly Gasket; Bulb; BULB |
| vcm | electrical | 19 | 7 | 0.875 | 2.42 | 0.433 | Bolt Vcm; Cmq Vcm; ECU-VCM |
| מתגי | electrical | 18 | 51 | 1.0 | 6.38 | 0.713 | 70 'פנל מתגים בדלת ד' שמ; C600 'בית מתגים שמ; Combination |
| belly | body-exterior | 18 | 19 | 1.0 | 6.6 | 0.825 | mopar belly pan extension rear; mopar belly pan front; mopar extension front belly pan |
| extinguisher | safety-systems | 17 | 9 | 1.0 | 2.0 | 0.48 | Bracket Fire Extinguisher N 9100g82gt3; Extinguisher; Extinguisher Holder |
| ravenol | fluids | 17 | 7 | 1.0 | 2.61 | 0.595 | CHEVROLET Cruze Hatchback II (J405) engine cooling system RA; CHEVROLET S10 II Standard Cab Pickup Antifreeze RAVENOL 1410; CHEVROLET Silverado 3500 HD Standard Cab Pickup Antifreeze R |
| פעמון | clutch-drivetrain | 16 | 389 | 0.973 | 29.7 | 0.887 | 1.4 פעמון לציריה פנימי ימין; 1.4 פעמון לציריה פנימי שמאל; 1.4 פעמון לציריה קד' חיצוני |
| aircleaner | air-conditioning-heating | 16 | 228 | 0.97 | 4.25 | 0.708 | Aircleaner; Aircleaner Assembly LH; Aircleaner Case Retaining Clip |
| lady | merchandise | 16 | 127 | 0.948 | 4.67 | 0.56 | BRAKE PAD SET DISC BRAKE 51121 FOR NISSAN DATSUN/SAKURA 280Z; BRAKE PAD SET DISC BRAKE KBP-6582 FOR INFINITI NISSAN 370Z/F; Fairlady Z Nismo Model Car 1 43 |
| בלוק | engine | 16 | 44 | 0.8 | 37.47 | 0.893 | 1X21 EUGAB טבעת בלוק מנוע; ASA-Bolt; COLB-LEWO פין לבלוק מנוע |
| חדשים | fasteners | 16 | 4 | 1.0 | 7.5 | 0.638 | Engine Piston Wrist Pin; Fender Liner Rivet; אום,דגמ'חדשים |
| suns | interior-comfort | 15 | 144 | 1.0 | 2.44 | 0.624 | 370z Cpe Sunshield; Altima 13 Sunshield Pink; Altima 13 Sunshield Rose |
| sight | body-exterior | 15 | 69 | 0.958 | 3.11 | 0.461 | A.B.S. 220547 HONDA Insight I Coupe (ZE) suspension ball joi; ACKOJA A26-1142 Honda Jazz GE control arm; AISIN JAJHO-003 Honda Insight ZE2/ZE3 tie rod |
| fog | lighting | 15 | 41 | 0.953 | 8.37 | 0.871 | 3RG 79618 Frame, fog light; 9-3 Sports 03′-07′ all models – Fog light RH; ABAKUS 037-34-454 Opel Astra H bumper grill |
| klokkerholm | body-exterior | 15 | 18 | 0.857 | 8.59 | 0.691 | KLOKKERHOLM 00180060A1 Headlight motor; KLOKKERHOLM 0019225 Front splitter Front; KLOKKERHOLM 0031314 Wing fender Right Front |
| zrf | wheels-bearings | 15 | 7 | 1.0 | 7.5 | 0.789 | 285 35zrf 20; 285 35zrf20; Dunlop 255 35zrf20 97y |
| oea | wheels-bearings | 14 | 142 | 0.947 | 79.47 | 0.979 | oea 175 65r14; oea 175 65r14 ge; oea 185 60r15 du |
| priz | electrical | 14 | 18 | 0.818 | 25.0 | 0.862 | Mirror Rear View Frameless Prizm; Nexen N Priz Ah8 Bw; N Priz Ah5 |
| צילינדרים | engine | 14 | 14 | 0.933 | 5.17 | 0.62 | 5 צינור דלק לצילינדרים 4 ו; אטם למערכת שמן למנועי 6 צילינדרים; אטם סעפת יניקה למנועי 5 צילינדרים |
| focus | suspension-steering | 14 | 13 | 1.0 | 2.5 | 0.248 | ABAKUS 017-017-0049 Ford Focus Mk3 Estate engine radiator; ABAKUS 120-02-077 Ford Focus mk1 Saloon abs sensor; ABAKUS 140-01-132 FORD Focus Mk3 Box Body / Hatchback power  |
| סטופ | electrical | 14 | 4 | 0.8 | 3.0 | 0.477 | AGM 40 סטרטסטופ AH מצבר; F32 'סטופר דלת קד' ימ; GASKET |
| camry | suspension-steering | 13 | 25 | 0.862 | 3.05 | 0.315 | ABAKUS 112-1162LMLEMN1 TOYOTA Camry VIII Saloon (_V7_, _VA7_; ABAKUS 212-1176L-LD-E TOYOTA Camry IV Saloon (_V2_) headligh; ABAKUS 212-11Q6R-LD-EM Toyota Camry XV40 headlights |
| micra | suspension-steering | 13 | 20 | 1.0 | 2.53 | 0.25 | 3RG 82640 Nissan Micra 5 engine thermostat; ABAKUS 132-035-003 Nissan Micra Mk3 central locking system; ABAKUS 141-01-228 Nissan Micra K10 wheel bearing |
| verso | suspension-steering | 13 | 20 | 1.0 | 2.57 | 0.248 | 3RG 48202 TOYOTA ProAce Verso (MPY_) cylinder head; ABAKUS 103-06-008 Toyota C-HR wiper motor; ABAKUS 552-1154RMLEMN2 TOYOTA Proace City Verso headlights |
| vkd | suspension-steering | 13 | 16 | 1.0 | 34.53 | 0.948 | 131 Panorama Track rod end SKF VKDY 312062; 147 Panorama Track rod end SKF VKDY 312039; Alto K10 Track rod end SKF VKDY 816006 |
| אמפר | electrical | 13 | 10 | 1.0 | 42.5 | 0.919 | 60 אמפר START STOP מצבר; AGM20 מצבר 95 אמפר; ALTERNATOR ASSY |
| effects | body-exterior | 13 | 9 | 0.818 | 2.11 | 0.603 | Fender Effects; Fender Effects (Black Edition); Fender Effects, Ralliar |
| modesty | interior-comfort | 13 | 9 | 0.9 | 13.0 | 0.867 | Modesty; Modesty Panel |
| textile | interior-comfort | 13 | 8 | 1.0 | 8.14 | 0.838 | 1 Set Footmats Textile; 1 Set Footmats Textile Black; 1 Set Footmats Textile Black Carmine R |
| ספסל | interior-comfort | 13 | 8 | 1.0 | 2.96 | 0.643 | Cushion Cover; CUSHION,REAR SEAT; PROP, LID, JEDCO, 90/180D.8IN |
| tensioning | belts-chains | 12 | 401 | 1.0 | 16.6 | 0.874 | 1 Set Of Tensioning Belts; Generator Tensioning Wheel; Generator Tensioning Wheel Idler Gear |
| הזרקת | fuel-air | 12 | 277 | 0.972 | 3.59 | 0.628 | 2.2 מש' הזרקת סולר; BOITIER דייאמ AP הזרקת דלק; HYUNDAI 1733225000B |
| closure | body-exterior | 12 | 40 | 0.889 | 7.11 | 0.662 | Cap Closure; Closure; CLOSURE-30787114 |
| מער | electrical | 12 | 33 | 0.917 | 3.1 | 0.369 | 1L חומר שטיפת מערכות מיזוג BELNET; 5L חומר שטיפת מערכות מיזוג BELNET; AP EUQSALF מערכת מרווחים |
| dpa | body-exterior | 12 | 16 | 1.0 | 2.47 | 0.507 | Aluminium Multi Drilled Adpator Plate - for ALL 4.5 inertia ; AUDI Q5 SUV Sportback (80A) Rear light DPA 99451826902; condenser-80110sdpa61 |
| qrt | fluids | 12 | 14 | 1.0 | 8.0 | 0.727 | cvt fluid fe qrt; Cvt Fluid Fe Qrt; diff gear oil lt qrt |
| indent | fasteners | 12 | 13 | 0.867 | 13.0 | 0.929 | Indent Hex With; Luggage Comp Indentation Par 519902; Us Indent Hex T |
| stabili | suspension-steering | 12 | 6 | 1.0 | 128.7 | 0.965 | 33 10 5966 SWAG Stabiliser Mounting; Active Stabilizer Front 313530; Bar Stabilizer |
| ebook | merchandise | 12 | 5 | 0.833 | 3.31 | 0.614 | 24h Le Mans 2016 Picturebook; Black Jaguar Notebook; Childrens Notebook |
| times | merchandise | 12 | 5 | 0.833 | 2.5 | 0.385 | DVD REPAIR TIMES; Line Clip 6 Times 889524; Porsche Times 01 |
| moly | fluids | 12 | 4 | 0.8 | 4.33 | 0.637 | 0w 20 Moly Full Synthet; Acadiane Manual gearbox oil LIQUI MOLY 4434; CHEVROLET Astro II Extended Cargo Van Antifreeze LIQUI MOLY  |
| suspens | suspension-steering | 12 | 4 | 1.0 | 16.52 | 0.915 | A.B.S. 210430 Suspension arm Front Axle Right, outer, Lower,; A.B.S. 212158 Suspension arm Rear Axle Left, Rear Axle Right; A.B.S. 220231 Renault Laguna 1 suspension ball joint |
| serpentine | belts-chains | 11 | 177 | 0.994 | 3.83 | 0.779 | AE MVB1050R4 Serpentine belt; AE MVB1110R4 Serpentine belt for vehicles with air condition; AE MVB1440R6 Serpentine belt for vehicles with power steerin |
| tedgum | suspension-steering | 11 | 38 | 0.884 | 2.21 | 0.453 | AUDI differential TEDGUM 00055708; Civic III Anti-roll bar link TEDGUM TED87509; DAEWOO Tico Hatchback Rubber Buffer, suspension TEDGUM 00165 |
| tourneo | suspension-steering | 11 | 25 | 1.0 | 2.08 | 0.253 | ABAKUS 232-01-146 FORD Transit Tourneo (FC_ _) shock absorbe; A.B.S. 17115 Ford Transit Tourneo brake discs; A.B.S. 220533 Ford Transit Tourneo MK6 suspension ball joint |
| vii | suspension-steering | 11 | 19 | 0.95 | 2.01 | 0.221 | 555 SB-H072 Honda Accord VII CP suspension ball joint; ABAKUS 042-017-0087-B MITSUBISHI Colt VII (VB) engine radiat; ABAKUS 218-1989R-UE SUZUKI Alto VIII Box Body / Hatchback (H |
| advertisement | merchandise | 11 | 12 | 0.923 | 6.0 | 0.75 | Dealer Advertisement; Reprint Advertisement 964 911 Carrera; Reprint Advertisement 993 911 Driving |
| resistive | electrical | 11 | 11 | 1.0 | inf | 1.0 | cord set resistive; Cord Set Resistive; Harness Resistive Plug |
| governor | engine | 11 | 10 | 0.909 | 4.27 | 0.461 | Automatic Gearbox Governor Drive Gear; body assy governor; Body Assy Governor |
| swift | suspension-steering | 11 | 10 | 0.833 | 1.85 | 0.23 | ABAKUS 218-1151L-LD-EM Suzuki Swift Mk4 headlights; ABAKUS 218-1170R-LD-EM Suzuki Swift AZ headlights; A.B.S. 200620 Suzuki Swift 2 wheel bearing |
| לגבר | merchandise | 11 | 9 | 1.0 | 5.2 | 0.634 | 46 מכנס לרוכב לגבר; 48 מכנס לרוכב לגבר; 50 לגבר SPORT מכנס עור |
| מרימי | engine | 11 | 5 | 1.0 | 78.0 | 0.975 | בורג למרימי שסתומים; מרימי נדנדים; מרימי שסתום |
| shower | air-conditioning-heating | 11 | 4 | 0.8 | 3.0 | 0.562 | Dog Travel Shower; Duct Foot Shower Rh; Duct Shower Foot Lh |
| לחגורת | safety-systems | 10 | 765 | 0.996 | 12.19 | 0.832 | 25 תפס שמ' לחגורת בטיחות; ADJUSTER-SEAT; ADJUSTER,SHOULDER |
| ערפל | lighting | 10 | 61 | 0.984 | 28.94 | 0.942 | 0 כיסוי ערפל שמ'בפגוש קד; 2010 CRV בית פנס ערפל קדמי ימין; 4D 1.0 כיסוי פנס ערפל שמ |
| זגוגית | body-exterior | 10 | 36 | 0.818 | 16.0 | 0.941 | Mirror Glass; Mirror Housing; SsangYong Korando - זגוגית מראה L-קורנדו |
| fiesta | suspension-steering | 10 | 19 | 0.95 | 2.32 | 0.251 | ABAKUS 017-017-0061 FORD KA+ Hatchback (UK, FK) engine radia; A.B.S. 17007 Ford StreetKA Convertible brake discs; A.B.S. 210184 FORD Fiesta Mk3 Courier (F3L, F5L) control arm |
| handbr | brakes | 10 | 12 | 0.923 | 2.53 | 0.707 | ABAKUS 231-05-070 Handbrake shoes Rear Axle, without lever; ABE C00527ABE Handbrake shoes Rear Axle; ABE CRM014ABE Handbrake shoes Rear Axle |
| underground | body-exterior | 10 | 12 | 1.0 | inf | 1.0 | Door Edge Guards Underground Gray; Door Edge Guard Underground; Rear Spoiler Underground Dark Gray |
| vun | body-exterior | 10 | 11 | 1.0 | 5.6 | 0.848 | AutoStyle DX VUN01 Front splitter |
| summer | wheels-bearings | 10 | 10 | 0.833 | 24.48 | 0.903 | 1 Set Complete Summer Wheels; 1 Set Complete Summer Wheels 1 Set Compl; 1 Set Complete Summer Wheels Black |
| covr | body-exterior | 10 | 8 | 0.8 | 2.17 | 0.289 | Bolt Front Covr; Bumper Covr Fit; Covr Actuator |
| steerin | suspension-steering | 10 | 8 | 1.0 | 44.91 | 0.923 | 9-3SS 07′-11′ all RHD 1.8T 2.0T (B207) Power Steering Rack; ABAKUS 134-01-002 Clock spring for steering wheel; ABAKUS 134-01-006 Clock spring for steering wheel |
| מצתים | engine | 10 | 8 | 1.0 | 6.74 | 0.842 | 2.2 מצתים ל; 2.2 מצתים למנוע; 2.4 מצתים למנוע |
| woodgrain | interior-comfort | 10 | 7 | 1.0 | 2.5 | 0.625 | Shift Knob Woodgrain; Trim Cover Panel Woodgrain 514041; Trim I Panel Ash Woodgrain 514041 |
| spidan | suspension-steering | 9 | 25 | 0.862 | 10.72 | 0.868 | AUDI A5 B10 Saloon (FU2) Steering rack gaiter SPIDAN 84284; AUDI e-tron GT Saloon Inner tie rod SPIDAN CHASSIS PARTS 651; Carry Truck DA Anti-roll bar link SPIDAN CHASSIS PARTS 57751 |
| guiede | air-conditioning-heating | 9 | 20 | 1.0 | 11.0 | 0.846 | Air Guiede Left 9100000f13; Air Guiede Left 9100000f82; Air Guiede Left 9100g82gt3 |
| avensis | suspension-steering | 9 | 17 | 1.0 | 3.03 | 0.298 | ABAKUS 142-01-615 Toyota Avensis Verso M2 timing chain; ABAKUS 212-11F3L-LD-EM Toyota Avensis t25 Wagon headlights; ABAKUS 212-11L1R-LDEM7 Toyota Avensis t25 Wagon headlights |
| picanto | suspension-steering | 9 | 13 | 0.929 | 2.81 | 0.283 | ABAKUS 140-01-320 Kia Picanto Mk1 power steering pump; ABAKUS 223-1167R-LDEM2 Kia Picanto JA headlights; ABAKUS 232-01-439 Kia Picanto Mk2 shock absorber |
| gril | body-exterior | 9 | 4 | 1.0 | 15.35 | 0.878 | ABAKUS 037-34-454 Opel Astra H bumper grill; ABAKUS 442-2509R-UDN Bumper grill Right, with hole(s) for fo; Air Outlet Grille Left C514080 |
| radiat | cooling | 9 | 3 | 1.0 | 53.34 | 0.952 | 9-3 98′-03′ all 4 cyl Petrol models – Top Radiator to Thermo; 9-3SS 03′-11′ 4 cyl petrol turbo models (B207) – Lower Radia; 9-5 02′-10′ 4 Cylinder Petrol Auto all models – Radiator |
| מעבה | air-conditioning-heating | 9 | 3 | 1.0 | 7.13 | 0.812 | a c condenser; A C Condenser; BEV F56 LCI מעבה מזגן |
| קישוטי | body-exterior | 8 | 1259 | 0.999 | 3.7 | 0.638 | Leapmotor Part 000000000006035802; RENAULT RE850704085R; RENAULT RE850764779R |
| signs | hybrid-ev | 8 | 39 | 0.975 | 2.0 | 0.5 | Daewoo Lanos - Dash Designs A19010E; Daewoo Lanos - Dash Designs B19010E; Designsketch J1 |
| nra | suspension-steering | 8 | 28 | 0.966 | 1.82 | 0.288 | AM NONRAHC צירים דלת אח; AM NONRAHC ציר מכסה גג; Bonrath BH 76216 Bumper Front, Paintable |
| חצוני | body-exterior | 8 | 21 | 1.0 | 1.93 | 0.578 | OEM Part - 'ידית לדלת חצוני אח' שמ; SsangYong Korando - פח אח' חצוני-קורנדו; SsangYong Musso - פח אח' חצוני-מוסו ספורט II |
| dispatch | suspension-steering | 8 | 16 | 0.941 | 2.2 | 0.228 | 3RG 48208 CITROËN Dispatch I Platform / Chassis cylinder hea; ABAKUS 009-025-0008 CITROËN Dispatch III Platform / Chassis ; ABAKUS 0537G02 CITROËN Dispatch I MPV glass for wing mirror |
| qcs | suspension-steering | 8 | 15 | 1.0 | 76.0 | 0.987 | Chevrolet Lacetti J200 Estate Coil spring QUINTON HAZELL QCS; Chrysler Crossfire Coupe Coil spring QUINTON HAZELL QCS5855; Chrysler Crossfire Roadster Coil spring QUINTON HAZELL QCS56 |
| malibu | suspension-steering | 8 | 12 | 0.8 | 1.96 | 0.279 | ABAKUS 037-025-0026 Chevrolet Malibu 8 engine thermostat; ABAKUS 135-1105LMLD-EM Chevrolet Malibu 8 headlights; A.B.S. 17255 CHEVROLET Malibu V brake discs |
| תקצוב | fluids | 8 | 10 | 0.909 | 2.0 | 0.5 | אטם ליח תקצוב נוזל אוראה למרסס; חיישן אוראה ביחידת תקצוב; חיישן לחץ ביחידת תקצוב אוראה |
| semiconduc | electrical | 8 | 8 | 1.0 | 21.0 | 0.955 | computer semiconduc; Computer Semiconduc; semiconductor relay module |
| activated | filters | 8 | 5 | 0.833 | 9.26 | 0.827 | 3F QUALITY 534 Pollen filter Activated Carbon Filter; ACKOJA A52-77-0011 Valve, activated carbon filter; ACKOJA A52-77-0017 Valve, activated carbon filter |
| halogen | lighting | 8 | 4 | 1.0 | 151.12 | 0.985 | ABAKUS 221-1154L-LDEM2 Headlight Halogen, Crystal clear, H1,; ABAKUS 221-1154L-LDEM2 Hyundai i30 CW headlights; ABAKUS 221-1154R-LDEM2 Headlight Halogen, Crystal clear, H1, |
| קבל | electrical | 8 | 4 | 1.0 | 3.83 | 0.404 | 48V קבל למערכת; Capacitor; CAPACITOR |
| בתות | suspension-steering | 7 | 446 | 0.993 | 6.0 | 0.571 | A EGAC בתות GIA הילוך ישיר; BAGUE בתות TNEC למשאבת שמן; bracket bushing |
| yokohama | wheels-bearings | 7 | 119 | 1.0 | 121.83 | 0.977 | Yokohama 175 65r14 82q; Yokohama 175 65r14 82t; Yokohama 185 55r16 83h |
| hankook | wheels-bearings | 7 | 19 | 1.0 | 19.45 | 0.922 | 225/55 R19 Original Tires (Hankook / COOPER TIRES) (COOPER T; 225/60 R18 Original Tires (COOPER / KUMHO / Hankook) (COOPER; 225/60 R18 Original Tires (COOPER / KUMHO / Hankook) (Hankoo |
| מצב | electrical | 7 | 10 | 0.833 | 53.59 | 0.917 | 14AH מצבר; 20 מצבר 70 א"ש ימ'יואסה; 40 מוסטאנג A FVP מצבר |
| california | suspension-steering | 7 | 9 | 1.0 | 2.47 | 0.25 | 1 Set Of Trim Foil California Gold; ABAKUS 053-017-0073 Volkswagen Tiguan III (CT1) engine radia; ABAKUS 053-025-0047 Volkswagen Golf VIII Variant engine ther |
| grll | body-exterior | 7 | 9 | 1.0 | 7.5 | 0.833 | Grll 04 07 Arm Tp 3; Grll 04 07 Tit Top; Grll 04 09 Qx56 Bot |
| xua | electrical | 7 | 9 | 1.0 | 8.0 | 0.727 | Plug Spark W16exua; RELAIS הליר XUA חשמלי לבלם; SSA XUAYUT צינור מים מתכת |
| מלוי | fuel-air | 7 | 9 | 0.818 | 1.83 | 0.373 | DUCT AY - OIL FILTER; SsangYong Korando - בית מלוי דלק-קורנדו; SsangYong Korando דלתית מלוי דלק |
| נוחות | interior-comfort | 7 | 8 | 0.889 | 2.0 | 0.571 | 43 נעלי נוחות; 44 נעלי נוחות; 45 נעלי נוחות |
| kraft | suspension-steering | 7 | 7 | 0.875 | 1.93 | 0.214 | Chevrolet Cruze j300 Tailgate strut KRAFT 8508347; Citroen C6 Saloon Track rod end KRAFT 4315527; DAEWOO Tico Hatchback Wheel bearing kit KRAFT 4107000 |
| overider | body-exterior | 7 | 7 | 0.875 | 5.0 | 0.714 | Front Bumper Overider Cover LH; Overider Beading 57"; Overider Bolt |
| parrot | audio-electronics | 7 | 7 | 0.875 | 3.67 | 0.688 | Parrot Chic; Parrot Ck 3000; Parrot Ck 3000 Bose |
| turnier | suspension-steering | 7 | 5 | 0.833 | 1.81 | 0.222 | ABAKUS 017-025-0044 Ford Focus IV HN engine thermostat; ABAKUS 431-11AJLMLDEM2 Ford Focus IV HN headlights; A.B.S. 200466 FORD Taunus ´80 Turnier (GBNS) wheel bearing |
| cvvt | engine | 7 | 4 | 1.0 | 2.0 | 0.381 | Bolt Cvvt; Bolt E Cvvt Cover; Cvvt Assy |
| yaw | electrical | 7 | 4 | 0.8 | 7.6 | 0.779 | 1. מנשא אופניים לוו גרירה 3 זוגות אופניים 2G yaworuE עם בסיס; 2008 Maserati Gran Turismo Yaw Rate Sensor 4.7 S Petrol 323k; 479300006R Sensor ESP Yaw Nissan Qashqai 1.6 D 96kW 6M 5P (2 |
| בקליט | lighting | 7 | 4 | 0.8 | 48.5 | 0.933 | ALOSI ELA בקליט מרווח; ALP TNAYOV בקליט פנס פנמי; DE VOYANT בקליט פנס איתות |
| סינון | filters | 7 | 4 | 1.0 | 2.08 | 0.431 | Engine Oil Pump Pickup Tube; Engine Oil Pump Pickup Tube And Screen; Engine Oil Pump Pickup Tube Gasket |
| runflat | wheels-bearings | 7 | 3 | 1.0 | inf | 1.0 | 225 35 R20 90y Pzero Runflat 369041; 225 40 R19 89y Pzero Runflat 369041; 245 45 R19 98y Pzero Runflat 369041 |
| switc | electrical | 7 | 3 | 1.0 | 9.73 | 0.657 | 3RG 80469 Hazard warning light switch; ABAKUS 135-02-030 Headlight switch without integrated regula; ABAKUS 135-04-006 Steering column switch |
| vkda | suspension-steering | 6 | 19 | 0.95 | 12.0 | 0.906 | AUDI 50 (86) Top strut mount SKF VKDA 40102; Audi A6 C7 Avant Top strut mount SKF VKDA 35123; Audi Q3 8u Top strut mount SKF VKDA 35122 |
| pajero | suspension-steering | 6 | 16 | 0.889 | 2.1 | 0.247 | ABAKUS 214-1186L-LDHEM Mitsubishi Pajero IV headlights; A.B.S. 212077 MITSUBISHI Pajero / Shogun Sport III Off-Road ; ACKOJA A37-0174 MITSUBISHI Pajero / Shogun Sport II Off-Road |
| talento | suspension-steering | 6 | 16 | 1.0 | 1.94 | 0.234 | 3RG 83658 Fiat Talento Van engine thermostat; ABAKUS 037-025-0044 Fiat Talento 296 engine thermostat; ABAKUS 042-017-0086 Fiat Talento 296 engine radiator |
| continental | wheels-bearings | 6 | 12 | 0.923 | 2.05 | 0.436 | 225 40 Z R19 Continental Spo 369041; Audi TT Roadster Wiper blade Continental 2800011309280; Citroën Berlingo K9 windscreen cleaning system Continental 2 |
| emr | suspension-steering | 6 | 12 | 0.923 | 5.36 | 0.711 | 3 Pack Emr Flre Liq; BMW Z8 E52 Top strut mount QUINTON HAZELL EMR6057; Chevrolet Spark M 150 Top strut mount QUINTON HAZELL EMR4799 |
| cabrio | suspension-steering | 6 | 11 | 0.917 | 2.14 | 0.246 | 2 Front Brake Caliper for Chrysler PT Cruiser Cabriolet Left; 2x Brembo front brake discs Maserati Gran Turismo 4.7 4.7 S,; 2x BREMBO FRONT BRAKE DISCS MASERATI GRAN TURISMO / GRANCABR |
| escort | suspension-steering | 6 | 9 | 0.9 | 1.97 | 0.227 | ABAKUS 017-025-0001 FORD Escort Mk3 Estate (AWA) engine ther; ABAKUS 431-11A4RMLDEM1 Ford Escort MK2 headlights; A.B.S. 200019 FORD Escort Express '91 (AVF) wheel hub |
| partner | suspension-steering | 6 | 9 | 1.0 | 2.22 | 0.257 | 3RG 48210 PEUGEOT Partner Origin Combispace (G_) cylinder he; 504 GRD Pickup Water pump and timing belt kit INA 530 0578 3; 555 SB-3602 PEUGEOT 1007 Hatchback suspension ball joint |
| magazin | air-conditioning-heating | 6 | 7 | 1.0 | 8.29 | 0.841 | CD Autochanger Magazine; Cd Magazine; Cd Magazine Ass |
| מיתלה | suspension-steering | 6 | 7 | 1.0 | 37.4 | 0.944 | OEM Part - 'בורג למיתלה אח; בולם זעזועים מיתלה ק; בולם זעזועים מיתלה קדמי |
| balljoint | suspension-steering | 6 | 6 | 1.0 | 3.38 | 0.736 | 900 94′-95′ Control Arm, incl Balljoint LH (A) – see descrip; 900 94′-95′ Control Arm, incl Balljoint RH (A) – see descrip; 900 94′-98′ & 9-3 98′-03′ BallJoint Boot Kit |
| tucson | suspension-steering | 6 | 6 | 1.0 | 2.1 | 0.25 | ABAKUS 019-017-0061-B HYUNDAI Tucson (TL, TLE) engine radiat; ABAKUS 024-017-0023-B HYUNDAI Tucson (TL, TLE) engine radiat; ACKOJA A52-0467 HYUNDAI Tucson (TL, TLE) gearbox mount |
| beacon | lighting | 6 | 5 | 1.0 | 5.0 | 0.833 | AMiO 01276 Flashing beacon; AMiO 01277 Flashing beacon; AMiO 02294 Flashing beacon |
| panhard | suspension-steering | 6 | 5 | 1.0 | 4.33 | 0.619 | 900 86′-93′ Powerflex Heritage Rear axle Panhard Rod, (Axle ; 900 86′-93′ Powerflex Heritage Rear axle Panhard rod (Body e; 900 86′-93′ Rear axle Panhard Rod, (Axle End) Bush (1) |
| suspensi | suspension-steering | 6 | 5 | 0.833 | 16.51 | 0.915 | A.B.S. 210430 Suspension arm Front Axle Right, outer, Lower,; A.B.S. 212158 Suspension arm Rear Axle Left, Rear Axle Right; A.B.S. 220231 Renault Laguna 1 suspension ball joint |
| probe | electrical | 6 | 4 | 0.8 | 3.0 | 0.725 | ASTA S-XOS5 Socket, lambda probe; Bracket For Lambda Probe Cab 116070; Bracket For Lambda Probe Cab C111070 |
| מרחק | electrical | 6 | 4 | 1.0 | 19.0 | 0.946 | F90 תושבת חיישן מרחק; Leapmotor Part 000000000005227897; Leapmotor Part 000000000155257934 |
| kumho | wheels-bearings | 6 | 3 | 1.0 | 285.0 | 0.997 | 225/55 R18 Original KUMHO Tire; 225/60 R18 Original Tires (COOPER / KUMHO / Hankook) (COOPER; 225/60 R18 Original Tires (COOPER / KUMHO / Hankook) (Hankoo |
| ligh | lighting | 6 | 3 | 1.0 | 12.41 | 0.807 | 1/4 Light Hinge Lh; 3RG 79618 Frame, fog light; 9-3 Sports 03′-07′ all models – Fog light RH |
| talosa | suspension-steering | 6 | 3 | 1.0 | 2.3 | 0.494 | AUDI A5 B10 Saloon (FU2) steering TALOSA 42-11095; AUDI A5 B10 Saloon (FU2) Track rod end TALOSA 42-11096; CHEVROLET Astra Saloon steering TALOSA 42-02687 |
| valvoline | fluids | 6 | 3 | 1.0 | 1.97 | 0.594 | Daewoo Leganza - Valvoline VV179; Daewoo Leganza - Valvoline VV955; E-Méhari Antifreeze Valvoline 874734 |
| רביעי | gearbox | 6 | 3 | 1.0 | 8.67 | 0.722 | 0 גלגל שיניים הילוך שלישי/רביעי; GEAR-MSHAFT 4TH; Repair kit, |
| לפנסי | lighting | 5 | 47 | 0.979 | 7.23 | 0.848 | accessory power relay; Cable harnesor; CONTROL |
| קידמית | body-exterior | 5 | 21 | 0.84 | 3.05 | 0.542 | ERTI שמשה קידמית; Frt Brake Disc; GENESIS 51712T1200 |
| laguna | suspension-steering | 5 | 18 | 1.0 | 2.05 | 0.248 | 3RG 82006 Renault Laguna 1 fuel pump; ABAKUS 042-017-0009 Renault Laguna 2 engine radiator; ABAKUS 042-017-0058-B Renault Laguna 2 Sport Tourer engine r |
| runner | suspension-steering | 5 | 13 | 0.812 | 2.47 | 0.304 | 1X BRAKE SHOE FITS: DAEWOO ARANOS CIELO CIELO LOAD RUNNER LE; 4 Piece All Weather Floor Mats 4runner; 555 SB-3881 TOYOTA Hiace Minibus (H50, H60, H70, H100) suspe |
| אריזה | wheels-bearings | 5 | 13 | 1.0 | 2.0 | 0.316 | CONNECTION F 12 באריזה; Engine mount 4 באריזה; Fuel filter 20 אריזה |
| skss | suspension-steering | 5 | 11 | 1.0 | 4.1 | 0.638 | Agile Top strut mount STARK SKSS-0670166; Audi A5 B8 Sportback Top strut mount STARK SKSS-0670102; BMW 1 Saloon (F52) Top strut mount STARK SKSS-0671342 |
| trafic | suspension-steering | 5 | 11 | 1.0 | 1.88 | 0.226 | 12 Van Tailgate strut RIDEX 219G0005; 12 Van Tailgate strut RIDEX 219G0075; 12 Van Tailgate strut RIDEX 219G0253 |
| caravan | suspension-steering | 5 | 10 | 1.0 | 2.35 | 0.224 | 2x BRAKE DISC 0 986 478 983 FOR PLYMOUTH DODGE GRAND/CARAVAN; 4MAX 0219-04-0044P Opel Astra F Caravan exhaust flex pipe; ABAKUS 442-1114L-LDEMN Opel Vectra B Caravan j96 Estate head |
| דיסקת | brakes | 5 | 9 | 0.818 | 3.14 | 0.72 | Crescent; HYUNDAI 31152L0000; SsangYong SsangYong - דיסקת אבטחה צנור בלם |
| פנסי | lighting | 5 | 9 | 1.0 | 13.74 | 0.849 | Air Vent Grille; Cable harnesor; CONTROL & ACT. |
| kblf | suspension-steering | 5 | 7 | 1.0 | 11.0 | 0.88 | C15 Top strut mount SNR KBLF41114; CHRYSLER Delta Hatchback Top strut mount SNR KBLF41769; Chrysler Neon PL Top strut mount SNR KBLF285 |
| qam | suspension-steering | 5 | 7 | 1.0 | 38.0 | 0.974 | 504 Pickup Top strut mount bearing QUINTON HAZELL QAM111; BMW Z8 E52 Top strut mount bearing QUINTON HAZELL QAM195; CHEVROLET Spark Box Body / Hatchback (M200, M250) Top strut  |
| connectr | electrical | 5 | 5 | 1.0 | 64.0 | 0.985 | Connectr Intake Manifold Water; Pack Connectr Asy 10; Pack Connectr Asy 11 |
| flashing | lighting | 5 | 5 | 0.833 | 12.0 | 0.857 | AMiO 01276 Flashing beacon; AMiO 01277 Flashing beacon; AMiO 02294 Flashing beacon |
| kuga | suspension-steering | 5 | 5 | 1.0 | 2.01 | 0.226 | ABAKUS 431-1184RMLD-EM Ford Kuga Mk1 headlights; ABAKUS 431-1197LMLD-EM Ford Kuga Mk2 headlights; A.B.S. 201649 FORD Kuga Mk1 (C394) wheel bearing |
| alcantara | interior-comfort | 5 | 4 | 0.8 | 6.32 | 0.671 | Alcantara Handbrake Boot 349900; Alcantara Interior Package; Alcantara Parking Brake Boot |
| ברגי | fasteners | 5 | 4 | 0.8 | 4.19 | 0.462 | 01 XEH SIV ברגים; (11588733 מדחס מזגן אקווינוקס)להזמין בנוסף ברגים; 1X7M H SIV ברגים |
| pemco | fluids | 5 | 3 | 1.0 | 47.0 | 0.959 | CHEVROLET Camaro VI Convertible Engine oil PEMCO PM0345-1; FORD Transit Mk3 Platform / Chassis (VE64) Engine oil PEMCO ; Mazda 6 Saloon Engine oil PEMCO PM0345-5 |
| אמובילייזר | electrical | 5 | 3 | 1.0 | 10.0 | 0.833 | ANTI THEFT FLASHER; Unit Assy Immobilize; VEHICLE IMMOBILIZER SWITCH |
| קריאה | lighting | 5 | 3 | 1.0 | 42.0 | 0.944 | OEM תאורת קריאה קדמית; READING LIGHT BRACKET ASSY; RING |
| בציימ | suspension-steering | 4 | 439 | 0.989 | 4.0 | 0.696 | GENESIS 55530T4000; LEXUS 4880348010; LEXUS 4882028050 |
| magazine | air-conditioning-heating | 4 | 60 | 0.923 | 7.14 | 0.82 | CD Autochanger Magazine; Cd Magazine; Cd Magazine Ass |
| sasic | suspension-steering | 4 | 27 | 1.0 | 3.31 | 0.478 | Citroen C25 280 Seal, spark plug stem SASIC 2130030; Citroen C3 Aircross 2 steering SASIC 7670043; CITROËN C4 III Hatchback Suspension arm SASIC 7470076 |
| kavo | suspension-steering | 4 | 15 | 1.0 | 1.97 | 0.258 | B-Series Timing Belt KAVO PARTS DTB-4006; Cadenza VG Deflection / guide pulley, V-ribbed belt KAVO PAR; CHEVROLET engine mount KAVO PARTS EEM-1066 |
| sunny | suspension-steering | 4 | 14 | 0.933 | 2.19 | 0.28 | 555 SB-4742 Nissan Sunny Traveller Y10 suspension ball joint; ABAKUS 215-1140L-LD-EM Nissan Sunny III Liftback (N14) headl; A.B.S. 15945 Nissan Sunny 2 N13 brake discs |
| cush | interior-comfort | 4 | 10 | 0.909 | 6.2 | 0.81 | Cover 2nd Cush Otr I; Cover, 3RD Seat Cushion,; Cover-cushio |
| croma | suspension-steering | 4 | 9 | 1.0 | 1.96 | 0.232 | ABAKUS 016-017-0065 FIAT Croma Van (194) engine radiator; ABAKUS 037-017-0026 Fiat Croma 194 engine radiator; ABAKUS 037-025-0001 Fiat Croma 194 engine thermostat |
| פעולה | electrical | 4 | 9 | 1.0 | 13.0 | 0.765 | Door Actuator; ידית מצב פעולה,נירו 23H; ממסר פעולה לסירוגין מגבים |
| קורא | electrical | 4 | 9 | 0.818 | 5.0 | 0.652 | EL6 NFC קורא; ET7 עם קורא NFC כבל; SIM Reader Pode |
| vectra | suspension-steering | 4 | 8 | 1.0 | 2.51 | 0.262 | ABAKUS 037-017-0035 Opel Vectra B CC engine radiator; ABAKUS 442-1114L-LDEMN Opel Vectra B Caravan j96 Estate head; A.B.S. 240399 Opel Vectra C Caravan tie rod |
| qashqai | suspension-steering | 4 | 7 | 1.0 | 2.24 | 0.231 | 1x Brake Pads Front for NISSAN MURANO 08- NAVARA 05- QASHQAI; 555 SR-N580 Nissan Qashqai J11 tie rod; A.B.S. 18460 Nissan Qashqai J12 brake discs |
| sharan | suspension-steering | 4 | 7 | 1.0 | 2.17 | 0.239 | 3RG 28712 VW Sharan 7n central locking system; ABAKUS 120-02-037 VW Sharan 1 abs sensor; ABAKUS 120-02-052 VW Sharan 1 abs sensor |
| םלוב | suspension-steering | 4 | 7 | 1.0 | 16.0 | 0.889 | םלוב UESSITROMA זעזועים אח; םלוב UESSITROMA זעזועים קד; םלוב UESSITROM זעזועים אח |
| crossfire | suspension-steering | 4 | 6 | 0.857 | 1.82 | 0.244 | ABAKUS 014-025-0003 Chrysler Crossfire Roadster engine therm; A.B.S. 200038 Chrysler Crossfire Roadster wheel bearing; A.B.S. 220142 Chrysler Crossfire Roadster suspension ball jo |
| miror | body-exterior | 4 | 6 | 1.0 | 7.5 | 0.75 | CAP-MIROR STAY LH; COVER CAP OUT MIROR LH; COVR-CAP MIROR LH |
| starlet | suspension-steering | 4 | 6 | 1.0 | 2.17 | 0.268 | ABAKUS 120-02-153 Toyota Starlet P9 abs sensor; A.B.S. 15853 Toyota Starlet 3 brake discs; A.B.S. 240285 Toyota Starlet P9 tie rod |
| echo | suspension-steering | 4 | 5 | 0.833 | 1.82 | 0.351 | 555 SR-6680 TOYOTA Echo Saloon (_P1_) tie rod; ABAKUS 232-02-081 TOYOTA Echo Saloon (_P1_) shock absorber; A.B.S. 17027 TOYOTA Echo Saloon (_P1_) brake discs |
| bose | audio-electronics | 4 | 4 | 1.0 | 4.0 | 0.64 | Cd Changer Mp3 Bose; Cd Changer Mp3 Non Bose; Connector Cable W Bose |
| eludo | electrical | 4 | 4 | 0.8 | 5.33 | 0.762 | A'D ELUDOM הצתה אלקטרונית; A'D ELUDO הצתה אלקטרונית; EDC ELUDOM מוסת למזגן |
| firewall | body-exterior | 4 | 4 | 1.0 | 2.09 | 0.519 | Cover Plate Firewall 517171; Firewall; firewall-5510148300 |
| marea | suspension-steering | 4 | 4 | 1.0 | 2.14 | 0.218 | ABAKUS 016-017-0015 Fiat Marea Weekend engine radiator; ABAKUS 016-025-0003 Fiat Stilo Estate engine thermostat; ABAKUS 661-1128R-LD-EM Fiat Marea Weekend headlights |
| nshb | suspension-steering | 4 | 4 | 1.0 | inf | 1.0 | Almera N17 Shock absorber dust cover FEBEST NSHB-N17F; Altima L31 Shock absorber dust cover FEBEST NSHB-L31F; Bluebird Sylphy G10/N16 Shock absorber dust cover FEBEST NSH |
| fndrs | body-exterior | 4 | 3 | 1.0 | 2.0 | 0.615 | paint pro fndrs ls; Paint Pro Fndrs Tc; paintprotc fndrs es |
| sway | suspension-steering | 4 | 3 | 1.0 | 2.29 | 0.531 | 9-5 98′-10′ (see descr) 21mm Sports Chassis Front Anti Roll ; Antisway Kit 829011; Bar Assy Anti Sway F |
| uessitrom | suspension-steering | 4 | 3 | 1.0 | 19.0 | 0.826 | UESSITROMA בולם זעזועים; UESSITROMA מאזן לחץ לדלק; הימוג UESSITROMA למע'מגבים |
| כיסויי | body-exterior | 4 | 3 | 1.0 | 5.23 | 0.687 | Bracket PDC ode; Cover Plate; F25 'ער' כיסויים למגן קד |
| טומל | suspension-steering | 3 | 439 | 0.991 | 3.0 | 0.706 | GENESIS 55530T4000; GENESIS 56400T1100; LEXUS 4880348010 |
| למבדה | electrical | 3 | 71 | 1.0 | 6.76 | 0.84 | 70 חיישן למבדה אחורי V; GYXO EDNOS סנסור למבדה; S40 2009-2010 חיישן למבדה אחורי |
| סטריקר | body-exterior | 3 | 57 | 1.0 | 5.75 | 0.73 | Lift Gate Lock Striker Plate; NI ENITAL סטריקר; OEM סטריקר לדלת תא מטען |
| sidem | suspension-steering | 3 | 32 | 1.0 | 11.78 | 0.892 | AUDI A5 B10 Saloon (FU2) Track rod end SIDEM 37632; AUDI A5 B10 Saloon (FU2) Track rod end SIDEM 37633; AUDI Q4 Sportback (F4N) Anti-roll bar link SIDEM 63065 |
| grill | body-exterior | 3 | 26 | 1.0 | 15.89 | 0.88 | ABAKUS 037-34-454 Opel Astra H bumper grill; ABAKUS 442-2509R-UDN Bumper grill Right, with hole(s) for fo; Air Outlet Grille Left C514080 |
| almera | suspension-steering | 3 | 17 | 1.0 | 2.57 | 0.276 | ABAKUS 042-025-0012 Nissan Almera Mk2 engine thermostat; ABAKUS 051-025-0002 Nissan Almera N15 engine thermostat; ABAKUS 215-11B7L-LDEMN Sentra 6 B16 headlights |
| clio | suspension-steering | 3 | 15 | 1.0 | 1.84 | 0.209 | 2x APEC Front Brake Discs for RENAULT CLIO III 2005-14 260mm; 2x BRAKE DISC 60013C FOR RENAULT CLIO/III/Grandtour/Sportour; 2x BRAKE DRUM 19-1356 FOR RENAULT CLIO/SYMBOL/II/Mk/MIO/Hatc |
| caddy | suspension-steering | 3 | 12 | 0.923 | 2.68 | 0.257 | 2x BRAKE DRUM 0 986 477 178 FOR VW PANEL/II/Box/Body/MPV VAN; ABAKUS 053-017-0073 Volkswagen Tiguan III (CT1) engine radia; ABAKUS 053-025-0047 Volkswagen Golf VIII Variant engine ther |
| fiorino | suspension-steering | 3 | 12 | 0.8 | 2.05 | 0.22 | ABAKUS 141-04-180 FIAT Fiorino MPV (225) cv joint; AC Rolcar 01.0505 Fiat Fiorino 3 window regulator; AISIN WE-FI03 Fiorino 127 water pump |
| hilux | suspension-steering | 3 | 12 | 1.0 | 2.22 | 0.27 | 555 SB-3561 Land Cruiser 100 suspension ball joint; A.B.S. 16553 Hilux V Off-Road brake discs; A.B.S. 201213 Toyota Hilux N30 wheel bearing |
| aygo | suspension-steering | 3 | 6 | 1.0 | 1.89 | 0.203 | 3RG 40287 Toyota Aygo AB10 engine mount; A.B.S. 200994 Toyota Aygo AB 40 wheel bearing; ACKOJA A70-07-0011 Toyota Aygo AB 40 wiper motor |
| estima | suspension-steering | 3 | 6 | 1.0 | 1.82 | 0.299 | ABAKUS 103-07-450 TOYOTA Previa / Estima II (XR30) windshiel; ABAKUS 142-01-625 TOYOTA Previa / Estima II (XR30) timing ch; A.B.S. 16033 Toyota Supra A70 brake discs |
| tepee | suspension-steering | 3 | 6 | 1.0 | 2.19 | 0.236 | 504 GRD Pickup Water pump and timing belt kit INA 530 0578 3; ABAKUS 552-1129LMLD-EM Peugeot Partner Tepee headlights; A.B.S. 37280 Peugeot Bipper Tepee brake pads |
| vkdp | suspension-steering | 3 | 6 | 1.0 | 53.0 | 0.981 | AUDI 50 (86) Dust cover kit, shock absorber SKF VKDP 43102 T; BMW E23 Dust cover kit, shock absorber SKF VKDP 33800 T; CHEVROLET Astra Saloon Rubber Buffer, suspension SKF VKDP 34 |
| סדן | body-exterior | 3 | 6 | 0.857 | 4.91 | 0.486 | fog light trim; SUZUKI P.35650.55G00; SUZUKI P.41801.55G50 |
| nappa | interior-comfort | 3 | 5 | 1.0 | 2.8 | 0.56 | Cover Base Backrest Nappa L 521041; Cover For Base Seat In Nappa 521041; Handbrake Lever Cover Nappa C344010 |
| alaskan | suspension-steering | 3 | 4 | 1.0 | 2.26 | 0.27 | 555 SB-N281 RENAULT Alaskan Pickup suspension ball joint; ABAKUS 141-04-133 RENAULT Alaskan Pickup cv joint; ABAKUS 142-01-604 RENAULT Alaskan Pickup timing chain |
| archive | merchandise | 3 | 4 | 0.8 | 2.75 | 0.733 | Bmw Mms Archive Jacket L Pro 9917800001; Bmw Mms Archive Jacket L Pum 9917800001; Bmw Mms Archive Jacket M Pro 9917800001 |
| uessitroma | suspension-steering | 3 | 4 | 1.0 | 14.0 | 0.824 | UESSITROMA בולם זעזועים; UESSITROMA מאזן לחץ לדלק; הימוג UESSITROMA למע'מגבים |
| מכשירים | interior-comfort | 3 | 4 | 1.0 | 6.14 | 0.827 | HYUNDAI 84701DF000T9Y; HYUNDAI 84710KL000YTH; בורג לתושבת לוח מכשירים |
| exhust | exhaust | 3 | 3 | 1.0 | 2.17 | 0.65 | Camshaft Assy Exhust; Catalt&Dnppe-Exhust |
| humidifier | air-conditioning-heating | 3 | 3 | 1.0 | 3.5 | 0.583 | Air Humidifier Left 511345; Air Humidifier Left 517110; Air Humidifier Right 511345 |
| ohm | electrical | 3 | 3 | 1.0 | 2.67 | 0.696 | Ballast Resistor, 1.6ohms; Ballast Resistor - 9.5 OHM; BERU EVL088 Distributor rotor arm Interference Suppression 1 |
| shc | suspension-steering | 3 | 3 | 1.0 | 1.9 | 0.225 | 4run Dashcam Mountin; A.B.S. 211897 Volkswagen California T6 Minibus (SGC, SGG, SH; A.B.S. 212143 Volkswagen California T6 Minibus (SGC, SGG, SH |
| veloster | suspension-steering | 3 | 3 | 1.0 | 1.88 | 0.234 | 555 SAK-8672L HYUNDAI Veloster (JS, JSN) control arm; ABAKUS 232-01-434 Hyundai Veloster FS shock absorber; ABAKUS 232-01-436 Hyundai Veloster FS shock absorber |
| גימור | body-exterior | 3 | 3 | 1.0 | 10.25 | 0.804 | 42 גימור מסגרת לחלוןקדימ; CHROME - CLADDING FRT WHEEL ARCH RH; CHROME - FRT BUMPER LOWER - LH |
| maxxis | wheels-bearings | 2 | 64 | 1.0 | 11.0 | 0.917 | 235/55 R18 Original Tires (MAXXIS / Giti / ATLAS); Maxxis M9502 369043; Maxxis T135 70d16 100m |
| pitman | suspension-steering | 2 | 20 | 1.0 | 27.4 | 0.919 | 555 SP-1620 Pitman Arm; 555 SP-1720 Pitman Arm; 555 SP-1855 Pitman Arm |
| טנק | fuel-air | 2 | 20 | 1.0 | 4.17 | 0.595 | DE BUTEE ימוג T דלת טנק דל; NA NOHCUOB מכסה טנק דלק; RIOVRESER טנק דלק |
| grandtour | suspension-steering | 2 | 14 | 1.0 | 2.24 | 0.251 | 2x BRAKE DISC 60013C FOR RENAULT CLIO/III/Grandtour/Sportour; 3RG 82640 Renault Talisman Grandtour engine thermostat; ABAKUS 120-03-090 RENAULT Modus / Grand Modus (F, JP) abs se |
| multivan | suspension-steering | 2 | 10 | 1.0 | 2.4 | 0.244 | ABAKUS 130-053-033 VW Multivan T5 window regulator; ABAKUS 130-053-034 VW Multivan T5 window regulator; ABAKUS 441-1168R-LD-EM VW Multivan T5 headlights |
| trail | suspension-steering | 2 | 9 | 1.0 | 8.28 | 0.702 | ABAKUS 231-01-392 NISSAN X-Trail (T33) brake pads; A.B.S. 211365 Suspension arm Rear Axle Left, inner, Upper, S; A.B.S. 212158 Suspension arm Rear Axle Left, Rear Axle Right |
| מסגרות | lighting | 2 | 9 | 1.0 | 7.8 | 0.848 | מסגרות לפנסי ערפל; מסגרות ערפל; סט מסגרות S60 T6 |
| carina | suspension-steering | 2 | 8 | 1.0 | 2.35 | 0.254 | 555 SR-3810 Carina T210 tie rod; 555 SR-7580 TOYOTA Corolla V Liftback (E80) tie rod; ABAKUS 212-1156R-LD-EM Toyota Carina E headlights |
| esprinter | suspension-steering | 2 | 8 | 1.0 | 7.45 | 0.558 | ABAKUS 054-017-0073 MERCEDES-BENZ eSprinter Van (B910) engin; A.B.S. 35277 Sprinter 5-t Platform / Chassis (907) brake pad; ALTE AUTOMOTIVE 83673AL Sprinter 3-t W910 tie rod |
| picasso | suspension-steering | 2 | 8 | 1.0 | 2.34 | 0.289 | 3RG 15226 Citroen Xsara Picasso suspension ball joint; A.B.S. 17336 Citroen Xsara Picasso brake discs; A.B.S. 220639 Citroen C4 Picasso mk1 suspension ball joint |
| shuma | suspension-steering | 2 | 8 | 1.0 | 1.87 | 0.226 | ABAKUS 132-024-001 KIA Shuma / Sephia II Saloon (FB) central; ABAKUS 223-1115R-LD-E KIA Shuma / Sephia II Saloon (FB) head; ABAKUS 223-1120R-LD-EM KIA Shuma / Sephia II Saloon (FB) hea |
| talisman | suspension-steering | 2 | 8 | 1.0 | 2.41 | 0.275 | 3RG 82640 Renault Talisman Grandtour engine thermostat; ABAKUS 232-02-335 Renault Talisman Saloon shock absorber; A.B.S. 201882 Renault Talisman Grandtour wheel bearing |
| traveller | suspension-steering | 2 | 8 | 1.0 | 2.62 | 0.278 | 3RG 80145 Nissan Sunny Traveller Y10 engine thermostat; 555 SB-4742 Nissan Sunny Traveller Y10 suspension ball joint; ABAKUS 141-01-217 Nissan Sunny Traveller Y10 wheel bearing |
| zafira | suspension-steering | 2 | 8 | 1.0 | 2.48 | 0.237 | 3RG 48202 OPEL Zafira Life (K0) cylinder head; ABAKUS 141-04-021 OPEL Zafira Life (K0) cv joint; ABAKUS 552-1149RMLDEM2 OPEL Zafira Life (K0) headlights |
| kangoo | suspension-steering | 2 | 7 | 1.0 | 2.17 | 0.249 | 2x Brake Discs Pair Vented fits RENAULT KANGOO 1.5D Front 20; 2x Brake Discs Pair Vented fits RENAULT KANGOO FW, Mk3 Front; 2x Front Floating Brake Caliper for Dacia Lodgy Logan MCV Re |
| sonata | suspension-steering | 2 | 7 | 0.875 | 2.23 | 0.284 | ABAKUS 140-01-359 Hyundai Sonata NF power steering pump; ABAKUS 141-03-118 HYUNDAI Sonata III (Y-3) wheel bearing; ABAKUS 221-1111L-LD-E HYUNDAI Sonata III (Y-3) headlights |
| eclipse | suspension-steering | 2 | 6 | 1.0 | 2.11 | 0.236 | 555 SB-8012 Mitsubishi Colt C50 suspension ball joint; 555 SR-B010 MITSUBISHI Outlander III Van (GF, GG) tie rod; A.B.S. 16501 Mitsubishi Eclipse D22 brake discs |
| bmper | body-exterior | 2 | 5 | 1.0 | 8.0 | 0.667 | Bmper Ppf Nohwpa; Bmper Ppf Nohw Vn; Bmper Ppf W Hw Vn |
| strake | body-exterior | 2 | 5 | 0.833 | 5.0 | 0.714 | Strake Assy Fr Wheel; Strake Kit; Strake L Rear |
| סוללות | electrical | 2 | 5 | 1.0 | 5.0 | 0.652 | F56 מחיצה בתא סוללות; F56 תפס למחיצת תא סוללות; Lifting Gear 813010 |
| combispace | suspension-steering | 2 | 4 | 1.0 | 2.38 | 0.293 | 3RG 48210 PEUGEOT Partner Origin Combispace (G_) cylinder he; 555 SB-6191 Peugeot Partner Combispace 5F suspension ball jo; ABAKUS 038-025-0007 Peugeot Partner Combispace 5F engine the |
| fluence | suspension-steering | 2 | 4 | 1.0 | 3.25 | 0.301 | 2x BRAKE DISC BR-6840-C FOR RENAULT DUSTER/SUV SCENIC/III FL; 2x Ventilated Brake Discs Front for DACIA DUSTER 2010-, RENA; 3RG 24605 Renault Twingo 2 gearbox mount |
| manta | suspension-steering | 2 | 4 | 1.0 | 2.22 | 0.294 | 555 SB-5282 Opel Manta B CC suspension ball joint; 555 SB-T502 Opel Manta B suspension ball joint; 555 SR-N760 Opel Manta B CC tie rod |
| refurb | interior-comfort | 2 | 4 | 0.8 | 2.5 | 0.333 | Carb (Front) Refurb; Dash Set Refurb; Dash Veneer Refurb |
| tins | merchandise | 2 | 4 | 1.0 | 3.08 | 0.705 | Campaign Iv Bullentins Jan 98 Dec; Martins Automatic; Martins Bead Seate |
| venza | suspension-steering | 2 | 4 | 0.8 | 2.19 | 0.318 | 555 SB-T432L TOYOTA Venza II (XU80) suspension ball joint; A.B.S. 35270 Venza AV10 brake pads; ACKOJA A70-0375 Venza AV10 control arm |
| xceed | suspension-steering | 2 | 4 | 1.0 | 2.07 | 0.237 | A.B.S. 18423 Kia Ceed Sportswagon CD brake discs; A.B.S. 32312 Kia Ceed Sportswagon CD abs sensor; A.B.S. 32313 Kia Ceed Sportswagon CD abs sensor |
| הלוגן | lighting | 2 | 4 | 1.0 | 5.61 | 0.783 | BULB; BULB H1 12V 55W P145S; BULB H7 12V 55W PX26D |
| רצועות | belts-chains | 2 | 4 | 1.0 | 2.75 | 0.638 | (A) רצועות גומי תפס מיכל דלק; BRACKET; (B) רצועות גומי תפס מיכל דלק |
| commuter | suspension-steering | 2 | 3 | 1.0 | 1.86 | 0.294 | 555 SR-H770 TOYOTA Hiace / Commuter Minibus (H200) tie rod; ABAKUS 231-01-370 Toyota Hiace 4 Van brake pads; A.B.S. 16615 TOYOTA HiAce Minibus (H300) brake discs |
| gfr | suspension-steering | 2 | 3 | 1.0 | 2.0 | 0.26 | A.B.S. 200321 FORD Scorpio Mk2 Saloon (GFR, GGR) wheel beari; A.B.S. 200322 FORD Scorpio Mk2 Saloon (GFR, GGR) wheel beari; A.B.S. 30097 FORD Scorpio Mk2 Saloon (GFR, GGR) abs sensor |
| jasm | suspension-steering | 2 | 3 | 1.0 | 1.97 | 0.261 | ABAKUS 120-02-075 Mazda 121 Jasm abs sensor; ABAKUS 140-02-014 Mazda 121 Jasm steering rack; ABAKUS 431-1128L-LD-EM Mazda 121 Jasm headlights |
| senator | suspension-steering | 2 | 3 | 1.0 | 2.14 | 0.349 | 555 SB-T432L Opel Senator A suspension ball joint; ABAKUS 140-01-182 Opel Senator B power steering pump; A.B.S. 200086 Opel Omega A Caravan wheel bearing |
| wish | suspension-steering | 2 | 3 | 1.0 | 23.86 | 0.85 | 9000 85′-98′ Control Arm / Wishbone RH – Alternative; 9000 85′-98′ Front wishbone bush kit (front & rear bushes); 9000 85′-98′ Front Wishbone (front) Bushing |
| בגב | interior-comfort | 2 | 3 | 1.0 | 6.33 | 0.679 | 22 'תפס בגב מושב כיסא קד; ARMREST ASY; Bezel Rear Seat Back |
| כנפון | body-exterior | 2 | 3 | 1.0 | 3.75 | 0.588 | Adapter for; Leapmotor Part 000000000735599036; OEM Part - כנפון לחלון גג |
| דיסקים | brakes | 1 | 82 | 1.0 | 12.0 | 0.8 | CD CHANGER JVC; DC מחליף דיסקים; OEM Part - נגן דיסקים |
| מסנו | filters | 1 | 34 | 1.0 | 3.5 | 0.609 | בית מסנו אוויר; בית מסנו אויר; מסנו אוויר SE416 |
| tshb | suspension-steering | 1 | 25 | 1.0 | inf | 1.0 | Alphard AH10 Shock absorber dust cover FEBEST TSHB-ACU20F; Alphard AH10 Shock absorber dust cover FEBEST TSHB-MCV30F; Alphard AH30 Shock absorber dust cover FEBEST TSHB-ASA44F |
| לדלק | fuel-air | 1 | 16 | 1.0 | 2.71 | 0.587 | BODY,THROTTLE; EP תוסף לדלק - PI; EXHAUST EMISSION CONTROL |
| קטליט | exhaust | 1 | 13 | 1.0 | 15.64 | 0.9 | BOLT; CATALYST ASY; CONVERTER |
| corona | suspension-steering | 1 | 9 | 1.0 | 1.89 | 0.3 | 555 SB-T442 TOYOTA Corona Premio X Saloon (ST210) suspension; 555 SB-T522 TOYOTA Corona Premio X Saloon (ST210) suspension; 555 SR-3580 TOYOTA Corona IX Saloon (T190) tie rod |
| liftback | suspension-steering | 1 | 9 | 1.0 | 2.85 | 0.267 | 555 SR-7580 TOYOTA Corolla V Liftback (E80) tie rod; ABAKUS 212-1140R-LD-E Toyota Corolla 1.6 Liftback headlights; ABAKUS 212-1183L-LD-EM Toyota Corolla e11 Liftback headlight |
| atos | suspension-steering | 1 | 8 | 1.0 | 2.11 | 0.249 | 555 SR-B170 Hyundai Atos MX tie rod; ABAKUS 035-025-0001 Hyundai Atos MX engine thermostat; ABAKUS 221-1110R-LD-E Hyundai Atos MX headlights |
| pony | suspension-steering | 1 | 8 | 1.0 | 1.94 | 0.221 | ABAKUS 221-1101L-LD-E HYUNDAI Pony / Excel Saloon (X-2) head; ABAKUS 221-1102L-LD-E Hyundai Pony X-2 headlights; ABAKUS 221-1102R-LD-E Hyundai Pony X-2 headlights |
| vios | suspension-steering | 1 | 8 | 0.889 | 2.52 | 0.256 | 555 SR-1890 TOYOTA Vios / Yaris II Saloon (XP9) tie rod; 555 SR-3880 TOYOTA Vios / Yaris II Saloon (XP9) tie rod; ABAKUS 212-11BZR-LEMN2 Toyota Yaris xp13 headlights |
| paseo | suspension-steering | 1 | 7 | 1.0 | 3.0 | 0.397 | A.B.S. 16617 Toyota Paseo EL54 brake discs; A.B.S. 200037 Toyota Paseo EL54 wheel bearing; A.B.S. 200757 TOYOTA Paseo (EL44) wheel bearing |
| carry | suspension-steering | 1 | 5 | 1.0 | 2.36 | 0.257 | A.B.S. 17163 Carry Truck DA brake discs; A.B.S. 200246 Suzuki Super Carry Minibus wheel bearing; A.B.S. 201165 Carry Truck DA wheel hub |
| cranksha | engine | 1 | 5 | 0.833 | 8.19 | 0.784 | 3RG 10400 Crankshaft pulley 6PK, Ø: 128mm; ABAKUS 120-04-051 Crankshaft sensor; ABAKUS 120-04-062 Crankshaft sensor |
| mokka | suspension-steering | 1 | 5 | 1.0 | 2.25 | 0.251 | ABAKUS 037-017-0121-B OPEL Mokka / Mokka X (J13) engine radi; ABAKUS 123-00-100 OPEL Mokka II cylinder head; A.B.S. 201625 OPEL Mokka / Mokka X (J13) wheel bearing |
| octavia | suspension-steering | 1 | 5 | 1.0 | 2.0 | 0.211 | ABAKUS 053-025-0038 Skoda Octavia 3 water pump; ABAKUS 130-003-072 Skoda Octavia 3 window regulator; ABAKUS 130-048-001 Skoda Octavia 1u5 window regulator |
| skcs | suspension-steering | 1 | 5 | 1.0 | 5.32 | 0.801 | CHEVROLET Corsa Saloon Coil spring STARK SKCS-0040463; Chrysler Crossfire Roadster Coil spring STARK SKCS-0040267; DOLZ SKCS129 Suzuki Grand Vitara FT timing chain |
| falken | wheels-bearings | 1 | 4 | 1.0 | 77.0 | 0.987 | FALKEN; Falken 215 55r18; Falken 215 60r16 95h |
| karl | suspension-steering | 1 | 4 | 1.0 | 2.38 | 0.287 | ABAKUS 037-025-0057 Opel Karl (C16) engine thermostat; ABAKUS 037-025-0058 Opel Karl (C16) engine thermostat; A.B.S. 18977 Opel Karl (C16) brake discs |
| magentis | suspension-steering | 1 | 4 | 0.8 | 1.98 | 0.285 | 555 SB-7842 Kia Magentis GD suspension ball joint; ABAKUS 140-01-315 Kia Magentis 2 power steering pump; ABAKUS 140-01-389 Kia Magentis 2 power steering pump |
| spacefox | suspension-steering | 1 | 4 | 1.0 | 2.29 | 0.294 | AS-PL S0731S Volkswagen Spacefox / Space Cross (5Z6, 5Z7, 5Z; BILSTEIN 19-156699 Volkswagen Spacefox / Space Cross (5Z6, 5; BILSTEIN 22-105813 Volkswagen Spacefox / Space Cross (5Z6, 5 |
| stratus | suspension-steering | 1 | 4 | 1.0 | 2.43 | 0.27 | 2x Brake Discs Pair Solid Rear For Dodge Stratus 2.4 16V Min; 2x Brake Discs Solid For Chrysler Stratus 2.4 16V Borg & Bec; 555 SA-6191R Chrysler Stratus JX Convertible control arm |
| ומצוף | fuel-air | 1 | 4 | 1.0 | inf | 1.0 | מקורי-מכלול משאבת דלק ומצוף; משאבה ומצוף במיכל דלק; משאבה ומצוף דלק |
| חצאית | body-exterior | 1 | 4 | 1.0 | 2.06 | 0.634 | HYUNDAI 86560G2100TMY; HYUNDAI 86577N7GA0PXP; HYUNDAI 86577Q0GA0 |
| batteries | electrical | 1 | 3 | 1.0 | 2.8 | 0.737 | Batteries Indus; BTS TURBO Battery YBX3000 SMF Batteries Battery Capacity: 60; KIT ACCESSORY-Batteries shelf protection |
| eqv | suspension-steering | 1 | 3 | 1.0 | 2.91 | 0.457 | ABAKUS 054-017-0073 MERCEDES-BENZ eSprinter Van (B910) engin; A.B.S. 220664 MERCEDES-BENZ Marco Polo Camper (W447) suspens; AIC 76194 MERCEDES-BENZ EQV (W447) abs sensor |
| mshb | suspension-steering | 1 | 3 | 1.0 | 19.0 | 0.95 | BMW 3 Saloon (G28) damping FEBEST BMSHB-F30F-KIT; BMW i4 (G26) Shock absorber dust cover FEBEST BMSHB-G01F; BMW Z4 E89 Dust cover kit, shock absorber FEBEST BMSHB-E90R- |
| nhp | suspension-steering | 1 | 3 | 1.0 | 2.45 | 0.419 | 555 SR-T410 TOYOTA Aqua I (NHP10) tie rod; A.B.S. 17748 TOYOTA Aqua I (NHP10) brake discs; A.B.S. 200996 TOYOTA Aqua I (NHP10) wheel bearing |
| nhw | suspension-steering | 1 | 3 | 1.0 | 2.85 | 0.363 | ABAKUS 232-01-533 Toyota Prius NHW11 shock absorber; A.B.S. 17543 Toyota Prius NHW11 brake discs; A.B.S. 31039 Toyota Prius NHW11 abs sensor |
| sintra | suspension-steering | 1 | 3 | 1.0 | 2.0 | 0.28 | 555 SB-7232 OPEL Sintra (APV) suspension ball joint; ABAKUS 037-025-0017 OPEL Sintra (APV) engine thermostat; A.B.S. 16916 OPEL Sintra (APV) brake discs |
| thalia | suspension-steering | 1 | 3 | 1.0 | 2.25 | 0.256 | 0 986 475 837 DRUM WHEEL BRAKE CYLINDER REAR BOSCH FOR RENAU; ABAKUS 042-017-0006 RENAULT Symbol / Thalia II engine radiat; ABAKUS 042-025-0001 Logan II engine thermostat |
| tinted | body-exterior | 1 | 3 | 1.0 | 18.6 | 0.853 | 19 Inch 5 V Spoke Tinted Silver Alloy; 21 Inch 10 Spoke Turbine Tinted Silver; ABAKUS 0410M02 Wing mirror grey, Blue-tinted, Convex, Heatab |
| בולמי | suspension-steering | 1 | 3 | 1.0 | 7.2 | 0.673 | 41 XEH SIV בורג לבולמים; Air supply dode; Air suspension controller |
| בתיבת | gearbox | 1 | 3 | 1.0 | 12.91 | 0.84 | # 19 'פלנטרי בתיבת הילוכים אדג; 3 בורג בתיבת הילוכים פוקוס; Automatic Transmission Filter O Ring |
| להזרקת | fuel-air | 1 | 3 | 1.0 | 8.4 | 0.737 | RAC EBU צינור להזרקת דלק; Relay; RELAY,MAIN |
| להרכבה | body-exterior | 1 | 3 | 1.0 | 3.5 | 0.583 | חיפוי גג לדגם 117.3 כולל חלק להרכבה; חיפוי גג לדגם 176 כולל חלק להרכבה; חיפוי גג לדגם 205.0 כולל חלק להרכבה |
| ruetcelfed | body-exterior | 0 | 37 | 0.949 | 3.5 | 0.5 | RUETCELFED דופן ק ימין; RUETCELFED טבעת לדרמים אח; RUETCELFED מגן רוח |
| aerodeck | suspension-steering | 0 | 8 | 1.0 | 2.03 | 0.26 | 2x BRAKE DISC 61-04-407 FOR ROVER HONDA CIVIC/Fastback/Aerod; ABAKUS 217-1132R-LD-EM Honda Civic Fastback headlights; A.B.S. 15983 Honda Prelude BA4 brake discs |
| sportswagon | suspension-steering | 0 | 8 | 1.0 | 2.4 | 0.295 | ABAKUS 223-1173LMLDEM2 Kia Ceed Sportswagon CD headlights; ABAKUS 223-1173RMLDEM2 Kia Ceed Sportswagon CD headlights; A.B.S. 16304 Toyota Carina E Sportswagon brake discs |
| zwt | suspension-steering | 0 | 8 | 0.889 | 3.07 | 0.728 | Chrysler 300c LX Suspension arm NTY ZWT-CH-024A; CHRYSLER control arm NTY ZWT-CH-055; Chrysler Sebring JR Suspension arm NTY ZWT-CH-001 |
| cougar | suspension-steering | 0 | 6 | 1.0 | 2.58 | 0.422 | A.B.S. 220061 Ford Cougar Coupe suspension ball joint; AKS DASIS 090710N Ford Cougar Coupe engine radiator; Alfa e-Parts AF03277 Ford Mondeo mk2 Estate abs sensor |
| premacy | suspension-steering | 0 | 6 | 1.0 | 2.38 | 0.29 | 555 SB-8012 Mazda Premacy cp suspension ball joint; 555 SR-9420 MAZDA Premacy Van (CP) tie rod; ABAKUS 120-03-015 MAZDA Premacy Van (CP) abs sensor |
| jetta | suspension-steering | 0 | 5 | 1.0 | 2.02 | 0.23 | 3RG 40703 VW Jetta MK1 engine mount; 3RG 40704 VW Jetta MK1 engine mount; ABAKUS 120-03-025 Volkswagen Jetta Mk7 Saloon (BU3) abs sens |
| klan | suspension-steering | 0 | 5 | 1.0 | 2.09 | 0.27 | ABAKUS 011-017-0011 Daewoo Nubira KLAN engine radiator; ABAKUS 037-025-0016 Daewoo Nubira KLAN engine thermostat; ABAKUS 140-01-104 Daewoo Nubira KLAN power steering pump |
| levin | suspension-steering | 0 | 5 | 1.0 | 2.27 | 0.338 | A.B.S. 210536 Corolla Levin E100 control arm; A.B.S. 240278 Corolla Levin AE92 tie rod; A.B.S. 240284 Corolla Levin E100 tie rod |
| palio | suspension-steering | 0 | 5 | 1.0 | 2.88 | 0.307 | ABAKUS 016-017-0024 Siena engine radiator; ABAKUS 231-03-050 Fiat Strada 178E brake discs; ABAKUS 661-1125R-LD-EM Fiat Palio Weekend headlights |
| slc | suspension-steering | 0 | 5 | 1.0 | 1.92 | 0.247 | A.B.S. 16047 HYUNDAI S-Coupé (SLC) brake discs; A.B.S. 240108 HYUNDAI S-Coupé (SLC) tie rod; ACEA A3 / B3 / B4-04; API SLCF |
| tahoe | suspension-steering | 0 | 5 | 1.0 | 2.4 | 0.34 | 4 Inch Lift Kit FOX 2.5 Performance Elite Coil-Over Aluminum; 4 Inch Lift Kit FOX 2.5 Performance Elite Coil-Over Cast Ste; 6 Inch Lift Kit Chevy/GMC Suburban Tahoe Yukon/XL 1500 15-19 |
| aurion | suspension-steering | 0 | 4 | 1.0 | 3.18 | 0.372 | 555 SR-T830 TOYOTA Camry VII Saloon (_V5_) tie rod; ABAKUS 141-01-261 TOYOTA Avalon Saloon (_X3_) wheel bearing; ABAKUS 232-01-525 TOYOTA Aurion (_V5_) shock absorber |
| elys | suspension-steering | 0 | 4 | 1.0 | 2.67 | 0.304 | 555 SR-6270 HONDA Elysion I (RR1-RR6) tie rod; ABAKUS 141-01-265 HONDA Elysion I (RR1-RR6) wheel bearing; ABAKUS 552-1142R-LD-EM CITROËN C-Elysée Saloon headlights |
| hshb | suspension-steering | 0 | 4 | 1.0 | inf | 1.0 | Chevrolet Captiva Sport Shock absorber dust cover FEBEST CHS; Chevrolet Matiz M200 Dust cover kit, shock absorber FEBEST C; CHEVROLET Optra Estate Shock absorber dust cover FEBEST CHSH |
| isam | body-exterior | 0 | 4 | 1.0 | 2.41 | 0.427 | ISAM 0103320 Cross Rail Rear; ISAM 0103470 Front Cowling Front; ISAM 0130612 Wheel arch liner Plastic, Left Front |
| scirocco | suspension-steering | 0 | 4 | 1.0 | 2.47 | 0.26 | 555 SB-5302 VW Scirocco 2 suspension ball joint; ABAKUS 120-09-014 VW Scirocco 2 knock sensor; ABAKUS 441-1175R-LD-EM VW Scirocco 1 headlights |
| berlingo | suspension-steering | 0 | 3 | 1.0 | 2.5 | 0.253 | 3RG 35201 Citroën Berlingo M tie rod; ABAKUS 009-025-0015 CITROËN C5 X III engine thermostat; ABAKUS 120-02-101 Citroën Berlingo M abs sensor |
| galloper | suspension-steering | 0 | 3 | 1.0 | 2.12 | 0.297 | 555 SB-7722R Hyundai Galloper 1 suspension ball joint; ABAKUS 233-03-502 Hyundai Galloper 1 suspension ball joint; A.B.S. 17965 Hyundai Galloper 2 brake discs |
| kubistar | suspension-steering | 0 | 3 | 1.0 | 1.98 | 0.245 | 3RG 80661 Nissan Kubistar Van X80 cylinder head; ABAKUS 042-025-0001 Nissan Kubistar Van X80 engine thermosta; ABAKUS 140-01-081 Nissan Kubistar Van X80 power steering pum |
| rifter | suspension-steering | 0 | 3 | 1.0 | 2.93 | 0.326 | ABAKUS 550-1171RMLDEM2 Rifter headlights; AKS DASIS 065030N Rifter turbocharger; AKS DASIS 155066N Rifter turbocharger |
| xad | suspension-steering | 0 | 3 | 1.0 | 2.12 | 0.243 | 205 Van Tailgate strut MAXGEAR 12-2029; 205 Van Wing mirror PRASCO PG0177004; ABAKUS 550-1103L-LD-E Peugeot 205 2 headlights |
| חליל | fuel-air | 0 | 3 | 1.0 | 5.12 | 0.631 | Distributor Fuel; DISTRIBUTOR,FUEL; Fuel Rail |
| ניצב | body-exterior | 0 | 3 | 1.0 | 34.67 | 0.897 | 11 קישוט ניצב דל' קד' שמ; ACTUATOR ASY; Applique |

## Category mismatch (1260)

Do NOT approve as proposed. Options: `דחהמילה <word>`, or (if the evidence category is right and the margin is clear) the owner could file it under the evidence category — `category_learning.approve_as()` exists in code but has NO owner-console command (verified by grep of agents/owner_console.py).

| token | proposed | evidence says | margin | share | obs | example classified parts |
|---|---|---|---|---|---|---|
| vest | engine | safety-systems | 1.06 | 0.329 | 129010 | Down Vest; Touch Up Paint Pen Vesta Blue; Vest |
| multi | air-conditioning-heating | engine | 1.02 | 0.328 | 36697 | 911 Gt3 Touring 992 2 1 43 Multi Coloure; 911 Gts 1 18 Multi Coloured; A.B.S. 30380 Fiat Multipla 186 abs sensor |
| lever | hybrid-ev | brakes | 1.02 | 0.219 | 22695 | 3RG 13242 Tensioner Lever, v-ribbed belt; 3RG 13702 Tensioner Lever, v-ribbed belt; 3RG 24707 VW Golf 1 repair kit gear lever |
| line | fluids | body-exterior | 1.2 | 0.173 | 22593 | 8 Slimline Washer; ABAKUS 053-16-052 Wheel arch liner Right Front; A.B.S. 17632 NISSAN Skyline Coupe (V35) brake discs |
| holder | wheels-bearings | lighting | 1.91 | 0.316 | 20849 | ABAKUS 054-15-862 Side indicator Right Front, Exterior Mirro; ABAKUS 216-1982L-UE Rear light Left, Outer section, without ; ABAKUS 217-2037R-UE Fog Light Right, without bulb holder, wi |
| out | air-conditioning-heating | body-exterior | 3.22 | 0.302 | 20802 | Toyota Land Cruiser Prado 90 electric system ABAKUS 212-2026; ABAKUS 212-11H9R-LD-EM Headlight Crystal clear, HB3/HB4, wit; Land Rover Outlet |
| pressure | fluids | fuel-air | 1.05 | 0.188 | 19981 | 1.980.011 EPS Sender Unit, oil pressure; 1PCS Brake Assist Pressure Sensor OEM 0265005329 For Nissan ; ABAKUS 232-01-033 Shock absorber Top eye, Bottom eye, Front  |
| keyless | gearbox | electrical | 2.17 | 0.497 | 17504 | AMiO 02637 Keyless Case; AMiO 02638 Keyless Case; AMiO 02639 Keyless Case |
| entry | gearbox | electrical | 1.38 | 0.362 | 17501 | "ANTENNA, PASSIVE ENTR ENTRY; "ANTENNA, Passive Entry, Rear Cargo Area; Bat Entry Card |
| boot | body-exterior | wheels-bearings | 1.02 | 0.215 | 16394 | 3RG 84245 Door Handle Boot; 911 Spirit 70 Football Boot Olive Black; Air Intake Boot |
| composite | lighting | air-conditioning-heating | 1.34 | 0.389 | 12129 | AUTOMEGA 190000710 Exhaust manifold gasket Fibre Composite; BGA CH1398 Head gasket Thickness: 1,5mm, Fibre Composite; BGA CH2351 Head gasket Thickness: 1,2mm, Fibre Composite |
| instrum | audio-electronics | electrical | 1.43 | 0.483 | 10871 | Bezel Instrument Pa; Block Assy Instrume; Clock For Instrument Housing Black |
| pic | air-conditioning-heating | body-exterior | 1.21 | 0.485 | 10213 | CHEVROLET Silverado 1500 IV Standard Cab Pickup (GMT1RC) Exh; Engine Oil Pump Pickup Tube; Engine Oil Pump Pickup Tube And Screen |
| down | merchandise | electrical | 1.17 | 0.219 | 10150 | 300TDI Defender and Discovery Front Down Pipe with Catalyst ; 9000 92′-98′ 2.0 2.3 Turbo w/ front placed CAT in Downpipe –; Accelerator Kickdown Cable |
| tie | merchandise | suspension-steering | 13.72 | 0.824 | 10147 | 08-16 Ford Superduty Through Shaft Stabilizer Performance Se; 17-On Ford Superduty Stabilizer 2.0 8.1 Inch Through-Shaft 1; 24-On Ineos Grenadier ATS Stabilizer 8.1 Inch Travel 23.2 In |
| reservoir | fluids | cooling | 1.13 | 0.248 | 10118 | ABAKUS 140-01-008 Power steering pump without reservoir; ABAKUS 140-01-072 Peugeot 206 SW power steering pump; ABAKUS 140-01-072 Power steering pump with reservoir |
| stopper | wheels-bearings | body-exterior | 2.09 | 0.325 | 8855 | Applique Panel Stopper; Arm And Bracket Stopper; Axle Beam Stopper |
| nozzle | air-conditioning-heating | fuel-air | 2.48 | 0.426 | 8049 | 3RG 84293 Seal Ring, nozzle holder; 9-5 02′-05′ Headlight Washer Nozzle Cover LH (PRIMED); 9-5 2006 to 2010 all models – Headlamp Washer Nozzle Right H |
| sleeve | hybrid-ev | brakes | 3.06 | 0.47 | 7803 | ABAKUS 120-09-020 Repair Sleeve, transmission output shaft (; A.B.S. 55045 Guide sleeve kit, brake caliper Rear Axle, Left; A.B.S. 55117 Guide sleeve kit, brake caliper Front Axle, Rig |
| facia | air-conditioning-heating | body-exterior | 1.11 | 0.512 | 7508 | Centre Panel Facia Moulding LHD; Cover Assy Ctr Facia; Cover-facia |
| חלו | electrical | body-exterior | 4.78 | 0.649 | 7127 | 05 כיסוי מסג' חלון קד שמ; *18 F150 'מנגנון לחלון אח; -20 'כיסוי מסגרת חלון אח |
| button | interior-comfort | electrical | 1.39 | 0.326 | 7065 | ABAKUS 103-07-280 Wiper blade Flat wiper blade, Front, Push ; ABAKUS 103-07-450 Wiper blade Flat wiper blade, Length: 450m; ABAKUS 103-07-480 Wiper blade Flat wiper blade, Length: 480m |
| defroster | air-conditioning-heating | body-exterior | 2.67 | 0.581 | 6705 | Actuator Defroster Flaps C644021; Arm Defroster Door; Cover Assy Defroster |
| rexton | electrical | body-exterior | 2.7 | 0.286 | 6620 | A.B.S. 240703 DAEWOO Rexton I (Y200) tie rod; A.B.S. 37242 DAEWOO Rexton I (Y200) brake pads; ACKOJA A51-80001 DAEWOO Rexton I (Y200) brake discs |
| service | service-general | gearbox | 1.31 | 0.24 | 6359 | 25 Service Gift Card; 900 94′-98′ Air Conditioning Hose – Service Hose; A C Service Valve |
| manual | accessories | gearbox | 3.57 | 0.466 | 6199 | 9-3 00′-03′ Manual Petrol – DO88 Intercooler Pressure Pipe &; 9-5 01′-10′ RHD B205, B235 MANUAL Brake Servo Vacuum Hose – ; ABAKUS 030-017-0014 Engine radiator Aluminium, Manual Transm |
| central | air-conditioning-heating | electrical | 2.64 | 0.585 | 5857 | 3RG 12600 Renault Espace 5 central locking system; ABAKUS 132-003-017 MG MGY Hatchback central locking system; ABAKUS 132-003-017 Saab 9-5 Estate central locking system |
| cluster | air-conditioning-heating | electrical | 1.75 | 0.426 | 5689 | Cluster; cluster assembly; Cluster Assembly |
| balance | wheels-bearings | air-conditioning-heating | 2.35 | 0.393 | 5650 | 9-3 Sports 03′ on 2.0 Turbo Models – Balance Chain Gear – Ge; Balance; Balance Link Bolt |
| joint | clutch-drivetrain | suspension-steering | 1.43 | 0.504 | 4816 | 555 SB-T432L ROVER 200 Hatchback (XW) suspension ball joint; ABAKUS 141-04-008 CV joint kit with grease lubrication, with; ABAKUS 141-04-016 CV joint kit with grease lubrication, with |
| lmp | lighting | body-exterior | 1.03 | 0.328 | 4773 | BRACKET-FOG LMP; Dom Hose Clmp 2; FOG LMP AY-FRL LH |
| hole | service-general | engine | 1.27 | 0.445 | 4715 | ABAKUS 053-10-451 Bumper grill Left Front, with hole(s) for ; ABAKUS 431-2513L-UD Bumper grill Left Front, with hole(s) fo; ABAKUS 441-2512L-UD Bumper grill Left, with hole(s) for fog  |
| pump | fuel-air | cooling | 1.38 | 0.357 | 4469 | ABAKUS 102-00-030 Oil Pump with shaft seal; ABAKUS 102-00-034 Oil Pump with gear; ABAKUS 103-02-003 Headlight washer pump |
| קליפסים | brakes | fasteners | 1.17 | 0.361 | 4345 | 21 כולל קליפסים ודסקיות D; 21 כולל קליפסים ודסקיות ח NLS D; 21 ללא קליפסים ודיסקיות חיכו D |
| audio | audio-electronics | electrical | 2.14 | 0.483 | 4064 | Amp-audio Po; Amplifier Assembly Audio; amplifier assembly premium audio |
| ornament | brakes | body-exterior | 2.96 | 0.486 | 3982 | Cover Cap Ornamental Grill 889527; Front Ornamental Grille 511311; Front Ornamental Grille Blac 511311 |
| doria | interior-comfort | fuel-air | 1.39 | 0.253 | 3914 | A.B.S. 36009 Volkswagen 412 Variant brake pads; CHEVROLET Silverado I 2500 Standard Cab Pickup Windscreen wa; Chrysler Voyager AS ignition / preheating MEAT & DORIA 10683 |
| meat | interior-comfort | fuel-air | 1.39 | 0.253 | 3914 | A.B.S. 36009 Volkswagen 412 Variant brake pads; CHEVROLET Silverado I 2500 Standard Cab Pickup Windscreen wa; Chrysler Voyager AS ignition / preheating MEAT & DORIA 10683 |
| suction | fluids | cooling | 1.16 | 0.301 | 3840 | A C Refrigerant Suction; a c refrigerant suction hose; A C Refrigerant Suction Hose |
| eye | merchandise | suspension-steering | 7.31 | 0.644 | 3831 | ABAKUS 232-01-033 Shock absorber Top eye, Bottom eye, Front ; ABAKUS 232-02-008 Shock absorber Top eye, Bottom eye, Rear A; ABAKUS 232-02-012 Shock absorber Top eye, Bottom eye, Rear A |
| impact | air-conditioning-heating | body-exterior | 2.53 | 0.479 | 3767 | 1 2 Impact Extension; 1 2 Impact Socket L; 1 2 Impact Socket M |
| pinion | gearbox | suspension-steering | 2.27 | 0.453 | 3465 | Adams Driveshaft JK Front 1310 CV Driveshaft Solid U-Joints ; APC EL200040-R Steering rack Electric, Rack and Pinion Steer; Automatic Transmission Differential Pinion Gear |
| עיבוי | fluids | cooling | 2.64 | 0.491 | 3340 | BRACKET,SUB TANK; CANISTER; CAP - RADIATOR |
| repair | service-general | brakes | 1.97 | 0.315 | 3338 | 3RG 26704 Audi A3 8P repair kit gear lever; ABAKUS 120-00-276 Cable Repair Set, tail light; A.B.S. 55373 Repair Kit, parking brake handle (brake caliper |
| isolator | body-exterior | engine | 1.36 | 0.256 | 3291 | Air Box Mounting Bracket Isolator; Auxiliary Pump Insulator; Battery Isolator |
| owner | accessories | engine | 1.11 | 0.348 | 3260 | 2008 Owner Retention; 911 991 Turbo Owner S Manual Ca My17; ABAKUS 223-1915R-UE KIA Towner Minibus rear lights |
| liquid | fluids | air-conditioning-heating | 1.17 | 0.326 | 3165 | 3m Overspray Masking Liquid 832520; 3RG 85796 Windscreen washer reservoir with bore hole for liq; ABRASIVE LIQUID FOR WIN |
| pocket | merchandise | lighting | 1.04 | 0.248 | 3151 | Box Assembly Pocket; Cap L Pocket; Classic Pocket T-Shirt- |
| גשר | suspension-steering | body-exterior | 1.01 | 0.283 | 3139 | 8M BME SIV בורג גשר קדמי; BOLT; BOLT AND WASHER |
| child | cooling | body-exterior | 2.14 | 0.459 | 3059 | Back Assy Child Res; Backrest Cover And Child Sea 519050; Bezel Assy Child |
| sems | service-general | fasteners | 1.96 | 0.477 | 3025 | 14x1 Hex Sems; 14x5 8 Sems W; Bolt Sems |
| park | hybrid-ev | engine | 1.44 | 0.412 | 2989 | 131-04-405 ASHIKA CABLE PULL, PARKING BRAKE LEFT FOR HONDA R; 243219-17673 DRUM WHEEL BRAKE CYLINDER REAR ATE FOR CHEVROLE; ABAKUS 120-01-110 Renault Master III Minibus parking sensors |
| stop | wheels-bearings | lighting | 1.3 | 0.285 | 2900 | 95E0011-OYO BRAKE LIGHT SWITCH STOP OYODO FOR DAEWOO NEXIA,E; Applique Panel Stopper; Axle Beam Stopper |
| anten | air-conditioning-heating | audio-electronics | 7.57 | 0.854 | 2883 | Antenna; ANTENNA; ANTENNA-32201811 |
| sens | air-conditioning-heating | electrical | 5.78 | 0.57 | 2873 | ABAKUS 120-02-035 Audi Q2 abs sensor; ABAKUS 120-08-038 Throttle position sensor; ABAKUS 120-08-137 Intake manifold pressure sensor |
| push | interior-comfort | electrical | 1.3 | 0.245 | 2836 | 1 4 Dot Push In U; 200TDI Valve and Push Rod Kit - Land Rover Defender, Range R; 3 8 Dot Push In U |
| hardware | engine | body-exterior | 2.0 | 0.336 | 2600 | ABS Hydraulic Assembly Hardware Kit; Air Deflector Hardware Kit; Air Deflector Hood Hardware Kit |
| תפוח | gearbox | suspension-steering | 11.18 | 0.821 | 2413 | 01M EXA בורג תפוח משולש; 4X2 (תפוח הגה ) פנימי; 4X4 תפוח הגה פנימי |
| צבע | merchandise | body-exterior | 2.97 | 0.484 | 2372 | 45-48 גרבי קיץ צבע חאקי; ainLock בצבע חאקי מידה M; Balaclava Siva anthracite M |
| בקבוק | merchandise | fluids | 1.97 | 0.355 | 2226 | 1300 'בקבוק בולם קד' שמ; a c receiver drier; ALE ELGNA תפס בקבוק מים |
| לתהל | fasteners | electrical | 1.32 | 0.184 | 2100 | AXIF EPAHC תושבת לתהל; OPD גלגל שיניים לתהל; OPD מחשב לתהל |
| tuner | audio-electronics | suspension-steering | 1.07 | 0.203 | 2098 | A.B.S. 18624 Fortuner brake discs; A.B.S. 32010 TOYOTA Fortuner II (AN150, AN160) abs sensor; AISIN BPTO-1017 Toyota Hilux III brake pads |
| valance | air-conditioning-heating | body-exterior | 2.4 | 0.647 | 2092 | 2 1/2 Valance; Air Duct Valance Panel Assembly RH; BRACKET-VALANCE |
| מירווח | fasteners | gearbox | 1.6 | 0.296 | 2081 | OEM Part - NLS טבעת מירווח לציר הנעה; OEM Part - טבעת למירווח למנעול דלת; OEM Part - טבעת מירווח לבולם זעזועים |
| visual | audio-electronics | air-conditioning-heating | 1.08 | 0.342 | 2077 | Audio visual entertainment system assemb; Control Assy Audio Visual; Control Asy Audio Visual Uni |
| retaining | service-general | body-exterior | 1.57 | 0.327 | 2067 | 2x Genuine Apec Brake Disc Retaining Screws For Audi; 2X REAR BRAKE DISC RETAINING SCREW FIT: RANGE ROVER SPORT 13; A.B.S. 96167 Spring Retaining Pin, brake shoe |
| mobil | fluids | electrical | 2.21 | 0.513 | 2066 | 3 Tier Mobile Rack; Cover Mobile Phone; Cradle Assy Mobile |
| intermediate | body-exterior | clutch-drivetrain | 1.15 | 0.225 | 2024 | 9000 94′-98′ intermediate exhaust pipe for front mount CAT; AIC 55726 Intermediate Shaft Front Axle, Right, for manual t; AJUSA 15004400 Shaft Seal, intermediate shaft |
| drive | clutch-drivetrain | belts-chains | 1.28 | 0.255 | 1968 | 3RG 15723 Axle Nut, drive shaft Front Axle; 780 V-Belt BOSCH 1 987 947 502; 780 V-Belt GATES 6227MC |
| positive | suspension-steering | electrical | 1.5 | 0.546 | 1924 | 123 Ignition System Distributor - Lucas 6 Cylinder Positive ; Alternator as C45 Dynamo Conversion Positive Earth; Battery Cable Positive Belo 611030 |
| לדוושה | wipers-washers | suspension-steering | 1.0 | 0.289 | 1901 | 8X6 EUGA פלסטיק לדוושה; ASCULEUR בוקסה לדוושה; BOLT |
| pot | fasteners | electrical | 1.08 | 0.323 | 1887 | 225 45 R17 91w Potenza S001 369041; 8mm Stc Spot We; 900 97′-98′, 9000 97′-98′, 9-3 98′-00′ Throttle Body Potenti |
| plastic | body-exterior | cooling | 1.31 | 0.2 | 1881 | 1 2 Inch Plastic Tape 759012; 1 2 Male Plastic Gla; 7" Chrome Surrounds. Plastic, Pair. |
| drip | fluids | body-exterior | 24.17 | 0.894 | 1843 | Cap Drip Mould; CAP, DRIP MOULD; Channel Roof Drip |
| חניה | accessories | electrical | 1.82 | 0.556 | 1842 | 4X4 כבל בלם חניה שמאל; Brake Backing Plate; CABLE ASY - PARKING BRAKE |
| לסנדל | accessories | brakes | 1.8 | 0.562 | 1842 | 600/650 אום לסנדלי סחיבה; מסמרה לחיבור רפידה לסנדל; מקורי-בטחונות פרסה לסנדלי בלם |
| gusset | service-general | body-exterior | 10.53 | 0.716 | 1835 | Center Floor Pan Gusset; Closing Plate Gusset; Control Arm Gusset |
| שטנגה | body-exterior | suspension-steering | 2.0 | 0.467 | 1805 | מוט מחבר שטנגה; קפיץ מחזיר לשטנגה; שטנגה |
| adjusting | service-general | fasteners | 2.46 | 0.365 | 1792 | Adjusting; Adjusting Bar C111070; Adjusting Bar C329900 |
| spec | body-exterior | electrical | 2.4 | 0.3 | 1716 | 1 Set Of Lock Cylinders Without Specific; 1st Vehicle Birthday Mailing Special Ed; ABAKUS 120-09-027 Camshaft Adjuster Intake Side, Requires sp |
| סורנ | brakes | body-exterior | 1.52 | 0.197 | 1715 | Access Cover; Antenna Assy Auto; Axle Assembly |
| adhesive | brakes | interior-comfort | 1.0 | 0.306 | 1692 | 1 Set Double Sided Adhesive Tape; 3m Adhesive 04248; Adapter Adhesive Kit |
| בנד | gearbox | fuel-air | 1.71 | 0.298 | 1671 | 54 REILLO בנד דוד פליטה; CLAMP; CLAMP - BOOT |
| dipstick | fluids | engine | 25.07 | 0.896 | 1611 | 3RG 80536 Oil Dipstick; 3RG 80785 Funnel, oil dipstick; 3RG 80789 Oil Dipstick |
| מיקרופון | audio-electronics | electrical | 1.24 | 0.311 | 1582 | F54 מיקרופון; HYUNDAI 96575G8100; HYUNDAI 96575G8300 |
| cayenne | accessories | air-conditioning-heating | 1.54 | 0.233 | 1572 | 18 Inch Cayenne Winter Wheel And Tire Set; 2022 Emissions Cayenne E Hybrid; 20 Inch Cayenne Design Winter Wheel And Tire |
| סינכרון | filters | gearbox | 1.32 | 0.412 | 1513 | 1 RUEDALA סינכרון; 1 טבעת סינכרון הילוך; 3 OLB UAENNA סינכרון הלוך |
| מחזיר | lighting | engine | 4.25 | 0.654 | 1492 | 7 R1200GS מחזיר שמן גל איזון; Axle Seal; Crankshaft front oil seal |
| hybrid | hybrid-ev | wipers-washers | 2.46 | 0.396 | 1476 | 2012 Infiniti M35H 3.5l Hybrid ABS Pump 2011-2013 476601MG1A; 2020 Optima Hybrid B; 2022 Emissions Cayenne E Hybrid |
| splitter | air-conditioning-heating | body-exterior | 35.32 | 0.911 | 1465 | ABAKUS 048-05-550 Front splitter Front; ABAKUS 053-37-590 Front splitter Front; Antenna Two Way Splitter |
| drivers | accessories | electrical | 1.16 | 0.358 | 1448 | 2012 Style Rear Lights RH (Drivers) for Range Rover L322; 2017 Infiniti Q30 1.5 Diesel 2wd Drivers Side Front Brake Ca; 2021-2025 DACIA SANDERO DRIVERS SIDE FRONT BRAKE CALIPER 410 |
| card | audio-electronics | air-conditioning-heating | 2.79 | 0.689 | 1443 | Bat Entry Card; Business Card Case; Cardan Shaft |
| prasco | air-conditioning-heating | body-exterior | 2.36 | 0.528 | 1408 | AVA COOLING SYSTEMS CR6146 Heater matrix with pipe; AVA COOLING SYSTEMS DN7539 Fan, radiator D1: 340mm; AVA COOLING SYSTEMS HD1291 AC expansion valve |
| sachs | gearbox | clutch-drivetrain | 1.05 | 0.494 | 1405 | Cadenza VG Top strut mount SACHS 803 386; Fiat Ducato 290 Platform Top strut mount bearing SACHS 801 0; FIAT E-Scudo Minibus Top strut mount SACHS 801 057 |
| adjus | wheels-bearings | interior-comfort | 1.56 | 0.31 | 1389 | ABAKUS 135-03-009 Switch, mirror adjustment; A.B.S. K24900 Clutch Cable with manual adjustment; A.B.S. K26510 Clutch Cable with manual adjustment |
| select | hybrid-ev | gearbox | 7.01 | 0.632 | 1363 | 3RG 23268 Selector- / Shift Rod; 3RG 23728 Gear selector cable Right, Left; ACKOJA A70-0594 Gear selector cable |
| flare | exhaust | body-exterior | 2.52 | 0.597 | 1351 | 1 2 Inverted Flare B; 1 X 1invflare X Mal; 3 8 Flare X Pol Adap |
| למרסס | gearbox | fuel-air | 1.29 | 0.278 | 1348 | Decoupling e; Decoupling Element 135060; Fuel Injector O Ring |
| telephone | accessories | electrical | 1.09 | 0.237 | 1344 | 9-3, 9-3SS & 9-5 with telephone; 9-3, 9-3SS & 9-5 with Telephone and SatNav Genuine Saab Shar; Adapter Assy Telephone |
| ilp | air-conditioning-heating | exhaust | 10.82 | 0.833 | 1336 | 900 84′-93′ Turbo exhaust tailpipe; AMiO 01302 Golf Mk6 tailpipe; AMiO 01302 Nissan Juke f15 tailpipe |
| כוהל | electrical | air-conditioning-heating | 4.11 | 0.561 | 1318 | 2010 CRV כוהל למדחס; ENIBOB LIF חוט לכוהל; LLA ENIBOB כוהל |
| veh | hybrid-ev | suspension-steering | 1.2 | 0.24 | 1310 | 1 Set Trim Strips For Vehicle Interior B; 1 Set Trim Strips For Vehicle Interior L; 1 Set Trim Strips For Vehicle Interior S |
| hybri | hybrid-ev | wipers-washers | 2.21 | 0.382 | 1302 | 2012 Infiniti M35H 3.5l Hybrid ABS Pump 2011-2013 476601MG1A; 2020 Optima Hybrid B; 2022 Emissions Cayenne E Hybrid |
| retain | fasteners | body-exterior | 2.91 | 0.387 | 1294 | A.B.S. 96167 Spring Retaining Pin, brake shoe; Air Conditioning Compressor Drive Clutch Retaining Ring; AUTOMEGA 160033510 Retaining Spring, coolant flange sealing  |
| bulk | fluids | body-exterior | 2.07 | 0.415 | 1267 | 345846 Left and 345845 Right Hand Bulkhead Vent Flap Set for; 42 - Land Rover Series Canvas Bulkhead Storage Bag with Tool; 5w 20 Base Bulk Keep Fu |
| חבק | filters | fuel-air | 1.06 | 0.174 | 1259 | BRACKET; Bumper Cover Grommet; BUSH |
| actyon | air-conditioning-heating | filters | 1.01 | 0.14 | 1230 | ABAKUS 141-01-125 SSANGYONG Actyon II wheel bearing; A.B.S. 17891 Ssangyong Actyon Sports II brake discs; A.B.S. 18226 SSANGYONG Korando IV Off-Road (C300) brake disc |
| idler | hybrid-ev | belts-chains | 12.85 | 0.806 | 1183 | 9-3 98′-02′ 4CYL Petrol all models : Idler Pulley (B) – Genu; 9-5 99′-10′ 4CYL all Petrol models (B205, B235): Idler Pulle; Accessory Drive Belt Idler |
| manaual | accessories | clutch-drivetrain | 6.17 | 0.804 | 1180 | Drivers Manaual; Drivers Manaual 911 Gt3 Gt3 Rs; Drivers Manaual 911 Gt3 Rs |
| מיכסה | exhaust | engine | 4.04 | 0.479 | 1178 | 01-2 מיכסה מנוע סטרים; 98-02 מוט מיכסה לאקור; AC LUCITRA ציר מיכסה בגז |
| בורר | suspension-steering | gearbox | 1.58 | 0.434 | 1174 | BALL BEARING; BUSHING; CABLE ASY |
| emission | exhaust | body-exterior | 1.18 | 0.407 | 1169 | 2018 911 Emissions Brochure; 2024 Emissions 718 Cw25; Emission |
| בקורה | lighting | body-exterior | 1.57 | 0.297 | 1151 | BRACKET - RADIATOR GRILLE; Guide Air; GUIDE,AIR |
| coat | interior-comfort | brakes | 4.35 | 0.565 | 1119 | 45 Gr Coated Zinc; A.B.S. 17233 Brake disc Coated, solid, Rear Axle; A.B.S. 17357 Brake disc Coated, solid, Rear Axle |
| מרכ | fasteners | body-exterior | 2.4 | 0.415 | 1114 | adapter for ode; Air duct wit; Armrest |
| dam | cooling | suspension-steering | 16.19 | 0.801 | 1108 | 780 Top strut mount bearing OPTIMAL F8-6267; ACKOJA A53-1530 KIA Sedona I (GQ) shock absorber; Aero Kit Front Air Dam |
| אבק | fuel-air | body-exterior | 1.04 | 0.264 | 1097 | 4X4 'מגן אבק צלחת בילום ק' ימ; Additional sor; Axle Assembly Dust Cap |
| איסוף | fuel-air | body-exterior | 1.67 | 0.385 | 1097 | AT מיכל איסוף אבק; מיכל איסוף אבק; מיכל איסוף וניקוז מים |
| שמש | wipers-washers | body-exterior | 3.4 | 0.67 | 1083 | ARM/BASE(R),WINDOW; F57 'שמשה דלת קד' שמ; F6 'גומי על' לשמשה קד |
| transpo | audio-electronics | suspension-steering | 1.48 | 0.18 | 1075 | 3RG 24792 T4 Transporter repair kit gear lever; 3RG 80700 T4 Transporter fuel pump; 555 SA-6191R VW T3 Transporter control arm |
| לחצן | clutch-drivetrain | interior-comfort | 1.18 | 0.339 | 1074 | BEZEL; BUTTON CAP; F350 ער' לחצן צופר |
| navig | air-conditioning-heating | audio-electronics | 3.05 | 0.604 | 1034 | 10inch Powered Sub-Woofer - 5-Door - W/O Navigation; Activation Of Navigation Data; Antenna Assy Naviga |
| שמשת | wipers-washers | body-exterior | 2.98 | 0.658 | 1032 | FRAME - SLIDING ROOF PANE; KIT - TILTING ROOF; Leapmotor Part 000000000077361217 |
| לניקל | interior-comfort | body-exterior | 4.0 | 0.7 | 1030 | AGRAFE תפס לניקל; AGRAFE תפס לניקל סף; ELIF UORCE קליפסים לניקל |
| קשת | interior-comfort | body-exterior | 1.31 | 0.546 | 1030 | AKK414-6 קשת כנף קדמי ימין; BRACKET(R),ROOF RACK; COVER - ROOF BOW |
| ונים | merchandise | interior-comfort | 6.08 | 0.738 | 1017 | (2007) זכוכית לוח שעונים; #250 פנל ללוח שעונים שחור; 4K3 מחוונים לבן F-500 קמפ 6170 |
| energy | hybrid-ev | body-exterior | 1.04 | 0.219 | 1012 | 235 55 R18 Energy Mxv4 S8 Bw 369041; 2LSELENIA DIGITEK PURE ENERGY-9.5553-GS1; 5LSELENIA DIGITEK PURE ENERGY-9.5553-GS1 |
| מבודד | audio-electronics | body-exterior | 2.36 | 0.408 | 1010 | 20 תפס למבודד כנף אקס; Acoustic covode; ANTISQUEAK - REAR SPRING |
| פלוס | fasteners | body-exterior | 1.54 | 0.34 | 1010 | Battery Cable Plus Pole 611030; Battery cablor; CABLE ASSY |
| carhartt | air-conditioning-heating | interior-comfort | 2.6 | 0.47 | 999 | Carhartt Beanie; Carhartt Beanie, Black, 0; Carhartt Beanie, Orange0 |
| defroste | air-conditioning-heating | body-exterior | 2.63 | 0.578 | 991 | Actuator Defroster Flaps C644021; Arm Defroster Door; Cover Assy Defroster |
| spare | body-exterior | wheels-bearings | 2.16 | 0.281 | 989 | Belt Spare Wheel T; Bracket Spare Wheel; C35 Pickup Glow plug DT Spare Parts 7.61101 |
| ראשי | engine | lighting | 6.68 | 0.777 | 976 | BRACKET(R),HEAD LAMP; BULB H7 12V 55W PX26D; Composite Assembly |
| היברידי | hybrid-ev | interior-comfort | 1.46 | 0.287 | 975 | filter-g92dh76020; Grille Sub Assy Rad; HYUNDAI 366003DBV0 |
| asi | hybrid-ev | body-exterior | 1.87 | 0.364 | 971 | Abrasion Protection 9100g82gt3; AKS DASIS 020033AL MG Midget MkIII engine radiator; AKS DASIS 020039AL MG MGB Convertible engine radiator |
| סרוו | electrical | engine | 3.83 | 0.59 | 949 | GENESIS 99240AR500; GENESIS 99240T1500; OEM מנוע סרוו |
| level | hybrid-ev | electrical | 1.01 | 0.291 | 947 | 3RG 95607 Sensor, wash water level; 4 Dr Vntvsr Level B; ABAKUS 003-026-002 Coolant expansion tank with coolant level |
| puddle | brakes | lighting | 4.0 | 0.767 | 940 | ABAKUS L52-420-001LED Wing mirror puddle light; Door Mirror Puddle/Approach Lamp Lh; Door Mirror Puddle/Approach Lamp LH |
| temperature | electrical | cooling | 2.83 | 0.634 | 930 | A C Evaporator Temperature Sensor; AIC 51908 Sensor, coolant temperature; AIC 75177 Thermostat with gasket/seal, Opening Temperature:  |
| pdm | electrical | cooling | 4.78 | 0.687 | 926 | ACKOJA A52-70-0032 Ignition lead set EPDM (ethylene propylen; ACKOJA A52-70-0036 Ignition lead set EPDM (ethylene propylen; BLUE PRINT ADBP810104 Steering rack gaiter Front Axle Right, |
| אינפרא | air-conditioning-heating | electrical | 17.0 | 0.895 | 926 | אוזניות )אינפרא לא אלחוטי(; מפתח שלט אינפרא; מפתח שלט אינפרא]762-[ |
| שיבות | body-exterior | suspension-steering | 1.0 | 0.154 | 913 | 1X67TNIOJ שיבות לצינור; 2 TNIOJ שיבות לצינור; AWD ROLLERS KIT |
| מפלג | gearbox | body-exterior | 2.18 | 0.363 | 912 | 4X64 TNIOJ אטם מפלג; CAPCITOR; CAP,DISTRIBUTOR |
| gas | exhaust | engine | 3.08 | 0.493 | 910 | NTY EGR-RE-000 EGR valve Electric, Solenoid Valve, with gask; TOPRAN 821 819 Exhaust manifold gasket Cylinder Head, Steel; VEMO V40-72-0075 Sensor, engine oil level with gasket/seal,  |
| metallic | air-conditioning-heating | body-exterior | 3.14 | 0.563 | 883 | 1 Set Of Trim Foil White Gold Metallic; 1 Set Trims Gentian Blue Metallic; 1 Set Trims Papaya Metallic |
| פרסוסטט | air-conditioning-heating | electrical | 1.25 | 0.357 | 875 | חיישן לחץ /פרסוסטט למזגן; חיישן לחץ קרר - פרסוסטט; פרסוסטט |
| silver | air-conditioning-heating | body-exterior | 1.87 | 0.313 | 864 | 18in 6 Double Spoke Sparkling Silver; 19in Silver Alloy Wheel; 1 Set Complete Summer Wheels Silver |
| decorative | air-conditioning-heating | body-exterior | 1.58 | 0.45 | 859 | Adapter Cable Decorative Tr 611015; Bright Decorative Strip In The Rear Bumper; Bumper Middle Decorative |
| קורנדו | exhaust | body-exterior | 1.49 | 0.202 | 847 | SsangYong Korando - אגזוז מר'-קורנדו II; SsangYong Korando - אגזוז קד'-קורנדו C; SsangYong Korando - אטם בית מסנן EGR-קורנדו II |
| רברס | fasteners | electrical | 1.08 | 0.224 | 842 | A ESREVART טרברסה; ALARM,BACK UP 51097DB 12V; AT טרברסה למנוע |
| distance | air-conditioning-heating | electrical | 1.07 | 0.373 | 836 | ABAKUS 042-57-451 Renault Master III Minibus bumper grill; Bracket Distance Sensor; Bracket Fwd Distance Wa |
| אוץ | electrical | brakes | 1.05 | 0.288 | 829 | AT RUETPAC כבל מד אוץ; CAT NONGIP גלגל מד אוץ; CAT NONGI גלגל מד אוץ |
| לאמש | clutch-drivetrain | lighting | 1.22 | 0.266 | 793 | actuator bracket; actuator clamp; applique panel |
| modulator | electrical | engine | 1.99 | 0.392 | 791 | 1997 DAEWOO NEXIA ABS Pump/Modulator; 2011-2024 CHRYSLER DELTA ABS Pump/Modulator 51918602; 2014 DACIA SANDERO STEPWAY ABS PUMP MODULATOR 476607745R |
| קרטון | merchandise | engine | 1.38 | 0.333 | 791 | 0.5L נוזל בלמים icer DOT4 בקרטון 20; 1LT שמן מנוע LukOIL 5W30 SN בקרטון 12; 4LT שמן מנוע 10W40 ממותג ISM בקרטון 6 |
| cross | electrical | body-exterior | 1.04 | 0.345 | 785 | 9-5 98′-10′ all models – Rear Upper Cross Stay/Control Arm; ABAKUS 037-016-0051 FIAT Urban Cross ac condenser; AJUSA 16097100 CITROËN C5 X III fuel pump |
| track | body-exterior | suspension-steering | 4.86 | 0.628 | 785 | 9-3 SS 03’on 1.8i, 1.9 TiD, 2.8 V6 (not XWD) Track rod end L; ABAKUS 233-11-537 Track rod end Front Axle Left; ABAKUS 233-11-538 Track rod end Front Axle Right |
| country | electrical | suspension-steering | 2.23 | 0.247 | 784 | A.B.S. 201893 OPEL Insignia B Sports Tourer Box Body / Estat; A.B.S. 220572 VOLVO V60 I Cross Country (157) suspension bal; AISIN FDE-VL08 Volvo XC70 Cross Country clutch flywheel |
| spray | accessories | fluids | 1.64 | 0.374 | 782 | 32oz bulk sprayer lv; 3m Hi Strength Spray; 3m Overspray Masking Liquid 832520 |
| strength | accessories | suspension-steering | 1.09 | 0.343 | 777 | 3m Hi Strength Spray; Enhanced Strength Tru Fit Dog Car Harness; Enhanced Strength Tru Fit Smart |
| altimax | fluids | air-conditioning-heating | 2.88 | 0.49 | 771 | 195 60 R15 88h Altimax Hp Bw 369041; 205 65 R15 94q Altimax Artic 369041; 215 55 R16 93h Altimax Hp 369041 |
| race | clutch-drivetrain | suspension-steering | 1.63 | 0.309 | 767 | 2x BRAKE DRUM 7D0138 FOR DAEWOO CIELO LANOS/SENS NEXIA LEMAN; Armrest L Front Door B81l Grace Blue; AS-PL S5105 Hyundai H1 Starex engine starter |
| סטליט | wheels-bearings | body-exterior | 1.19 | 0.24 | 763 | DIFFERENTIAL ASY; ETILLETA סטליט דיפרנציאל; OEM Part - בית סטליטים לדפרינציאל |
| api | fluids | engine | 1.58 | 0.228 | 762 | ABAKUS 130-003-063 Skoda Rapid nh1 window regulator; ABE CCZ1360ABE Skoda Rapid Saloon abs sensor; AE CAM962 Skoda Rapid Saloon camshaft |
| dash | air-conditioning-heating | interior-comfort | 4.07 | 0.632 | 746 | AMiO 02555 Honda CR-V Mk3 dashboard; AMiO 02555 Mercedes A124 dashboard; AMiO 02555 Opel Corsa E x15 dashboard |
| dust | brakes | suspension-steering | 4.65 | 0.672 | 731 | 2018 Dacia Duster 1.5HDI ABS Pump Unit - 478407227R; 2x Brake Discs Pair Vented fits DACIA DUSTER 1.0 Front 2019 ; 2x Brake Discs Pair Vented fits DACIA DUSTER 1.2 Front 13 to |
| נועלי | fasteners | suspension-steering | 1.14 | 0.32 | 729 | NUT - WHEEL; Set for rearode; בורג כיוון נועלים |
| bleeder | fluids | brakes | 2.81 | 0.564 | 727 | Bleeder; Bleeder Screw; Bleeder Screw 9100g82gt4 |
| להט | gearbox | engine | 3.85 | 0.663 | 718 | 9 מצת להט ח; CONTROL UNIT; E-46D מצת להט |
| למצתי | gearbox | engine | 3.0 | 0.637 | 718 | CONTROL UNIT; EGATOPAC ןגמ B גומי למצתים; NLS פיקוד למצתי להט |
| מראות | audio-electronics | electrical | 1.82 | 0.59 | 707 | 719 ער ברגים למראות; ACTUATOR(L); ACTUATOR L LH |
| tubular | cooling | fasteners | 1.77 | 0.46 | 700 | 4-Inch Round Tubular Assist Step Bar in Chrome; Assist Step Tubular Chrome; Clip Tubular |
| evo | hybrid-ev | fuel-air | 2.0 | 0.212 | 698 | 255 55r20 Hk Win Icept Evo3x Nc0; 285 35r21 Hk Ion Evo; 295 45r20 Hk Win Icept Evo3x Nc0 |
| boys | hybrid-ev | body-exterior | 2.32 | 0.575 | 696 | Boys Cap Gre; Boys Cap Nav; Boys Cap Ora |
| פיסטונים | merchandise | engine | 78.0 | 0.975 | 693 | C18 NOTSIP פיסטונים; C18 NOTSI פיסטונים; NOTSI 18 פיסטונים |
| vane | fuel-air | brakes | 1.04 | 0.155 | 686 | ABAKUS 100-1101N-LD-E Vanette C22 headlights; ABAKUS 215-19N1R-UE NISSAN Vanette Van (S20) rear lights; A.B.S. 201090 Vanette C22 wheel bearing |
| כלי | accessories | engine | 1.03 | 0.224 | 681 | 550 כלי להכנסת מחזיר שמן; Adapter 813010; ADAPTER CABLE CONN. HVTS |
| עבודה | accessories | engine | 1.43 | 0.185 | 672 | 21MYFLAT RATE MANUAL VER 1 CD; Axle Shaft Removal Tool; Brake Caliper Piston Reset Tool |
| illumination | lighting | air-conditioning-heating | 1.18 | 0.285 | 662 | Bed Cargo Illumination; Bracket Illumination Lamp Lh; Bracket Illumination Lamp Rh |
| racechip | air-conditioning-heating | suspension-steering | 12.34 | 0.827 | 660 | RaceChip 51936013 Chip tuning; RaceChip 51937010 Chip tuning; RaceChip 51937013 Chip tuning |
| tuning | air-conditioning-heating | suspension-steering | 12.15 | 0.823 | 660 | RaceChip 51936013 Chip tuning; RaceChip 51937010 Chip tuning; RaceChip 51937013 Chip tuning |
| differ | gearbox | clutch-drivetrain | 6.81 | 0.817 | 640 | automatic transmission differential bearing; Automatic Transmission Differential Bearing; Automatic Transmission Differential Carrier |
| lifting | engine | air-conditioning-heating | 1.12 | 0.244 | 638 | Battery pack lifting lug base (100 kwh); Bracket For Lifting Device 813010; Bracket For Sliding Lifting 889524 |
| אופטימה | fasteners | body-exterior | 1.67 | 0.248 | 625 | Accelerator Pedal Sensor; Belt Weather Strip; Black Out Tape |
| draining | fluids | gearbox | 2.71 | 0.637 | 621 | Draining; Draining Channe; Draining Hose |
| למניפה | safety-systems | clutch-drivetrain | 1.2 | 0.273 | 608 | C גלגל שיניים מקשר מנוע הידראולי למניפה; OEM Part - צמת חוטים למניפה; אום נעילה למניפה שופל |
| dem | wheels-bearings | suspension-steering | 2.63 | 0.598 | 605 | ABAKUS 112-1149LMLDEM2 Headlight HIR2, WY21W, LED, without b; ABAKUS 151-1101LMLDEM2 Renault Koleos 2 headlights; ABAKUS 151-1101RMLDEM2 Renault Koleos 2 headlights |
| wash | wipers-washers | fasteners | 1.34 | 0.364 | 602 | 10x1 2 Hexwashrheadt; 3RG 88814 Windscreen washer pump; 9-5 2006 to 2010 all models – Headlamp Washer Nozzle Right H |
| cayman | accessories | air-conditioning-heating | 2.36 | 0.3 | 595 | 2017 718 Boxster Cayman French Om; 2018 718 Cayman Gts Emissions Brochure; 20 Inch 718 Cayman Gt4 Set Of Wheels |
| טנד | safety-systems | body-exterior | 1.88 | 0.27 | 591 | 4X4 מצבר 19 טנדרים; 4X4 מצבר דגם 19 טנדרים; BULKHEAD ASSEMBLY |
| aluminum | fuel-air | wheels-bearings | 5.04 | 0.66 | 584 | 16 Wheel Aluminum; 16X6.5 חישוק; 17\\" Wheel, Aluminum |
| alenza | electrical | wheels-bearings | 3.4 | 0.567 | 583 | Alenza 001 Emt (Rft) Bw; Alenza A/S 02 Bw; Alenza A S Ultra B |
| differentia | gearbox | clutch-drivetrain | 8.9 | 0.873 | 582 | automatic transmission differential bearing; Automatic Transmission Differential Bearing; Automatic Transmission Differential Carrier |
| מייבש | wipers-washers | air-conditioning-heating | 3.29 | 0.57 | 575 | 2.2 מייבש למזגן; ACCUMULATOR; ACCUMULATOR ASY - AIR CON |
| motion | body-exterior | filters | 1.44 | 0.341 | 572 | AUGER 86005 Windscreen washer pump; Body Side Molding Emotional Red; CARMOTION 58054 Interior Mirror |
| skirt | brakes | body-exterior | 27.0 | 0.895 | 565 | 14FT HOPPER SKIRTING RUBBER; Black Noir Pearl Body Kit Rear Skirt; Body Kit Front Bumper Skirt |
| split | fasteners | body-exterior | 6.22 | 0.712 | 551 | 19 wheel split six spoke forged alloy; 20" Wheels, 5 Split-Spoke, Low Gloss Black; 4ft Cable With Universal Split Ring |
| welt | wheels-bearings | body-exterior | 9.89 | 0.718 | 549 | Cover Seaming Welt; mopar headlamp to grille welt left; mopar headlamp to grille welt right |
| aper | wheels-bearings | body-exterior | 2.54 | 0.431 | 539 | 1 Set Of Taper Roller Bearings; Aperture; Aperture Assembly |
| maste | fluids | body-exterior | 1.7 | 0.508 | 516 | A.B.S. 230920 Renault Master III Minibus track rod end; A.B.S. 61215 Brake master cylinder Ø: 22.2mm; A.B.S. 61225 Brake master cylinder Number of connectors: 2 |
| mok | merchandise | suspension-steering | 1.03 | 0.162 | 515 | 4411 DRUM WHEEL BRAKE CYLINDER PAIR REAR LPR 2PCS FOR ROVER ; 780 V-Belt KAMOKA 7010311; AUDI V8 (441, 442, 4C2, 4C8) Fuel filter KAMOKA F304801 |
| puller | accessories | fuel-air | 2.18 | 0.482 | 512 | Axle Shaft Puller; Ball Joint Puller; Ball joint puller D32 |
| vertica | suspension-steering | air-conditioning-heating | 1.92 | 0.378 | 502 | Actuator Vertical Seat Adjus C671010; Amplifier For Vertical Anten C651022; Awning Vertical Brac |
| mxv | hybrid-ev | wheels-bearings | 1.87 | 0.467 | 498 | 205 65 R15 94h Primacy Mxv4 369041; 215 55 R17 Energy Mxv4 S8 Bw 369041; 235 55 R18 Energy Mxv4 S8 Bw 369041 |
| לגל | fasteners | wheels-bearings | 7.58 | 0.648 | 484 | 16X6 חישוק גלגל; 2# מיסב לגל ארכובה; "32 כיסוי גלגל ברונקו |
| navi | interior-comfort | audio-electronics | 1.7 | 0.453 | 483 | 10inch Powered Sub-Woofer - 5-Door - W/O Navigation; Activation Of Navigation Data; Antenna Assy Naviga |
| vehicle | body-exterior | suspension-steering | 1.21 | 0.241 | 480 | 1 - Land Rover Series II/III 88&quot; Full Vehicle Premium C; 1 Set Trim Strips For Vehicle Interior; ABAKUS 037-34-454 Opel Astra H bumper grill |
| בוץ | fuel-air | body-exterior | 8.61 | 0.86 | 479 | 3.סט מגני בוץ קדמי; BAVETTE AR מגן בוץ אח; BOLT |
| ימדק | clutch-drivetrain | body-exterior | 1.06 | 0.165 | 479 | axle assembly; black out tape; bracket bushing |
| חיישני | safety-systems | electrical | 4.16 | 0.691 | 474 | 2 צמת חוטים לחיישני חניה; CORD; CORD,SHORT |
| scuff | interior-comfort | body-exterior | 1.26 | 0.411 | 468 | Anti Scuffing 889700; Brkt Rr Dr Scuff Mtg; Clamp Door Scuff Pl |
| courtesy | body-exterior | lighting | 12.93 | 0.824 | 455 | Body Dr Courtesy Lam; Courtesy; Courtesy Button, Beige |
| veneer | audio-electronics | body-exterior | 1.13 | 0.42 | 454 | Ashtray Veneer Panel; Black Veneer Kit LHD; Carbon Fibre Dahsboard Veneer Kit RHD |
| הרכב | audio-electronics | body-exterior | 2.85 | 0.424 | 452 | 3M RUBBERIZED UNDERCOATING; #*-90/81 תושבת להרמת הרכב; (A) צינור בלם מתכת לאורך הרכב |
| casting | audio-electronics | brakes | 1.33 | 0.333 | 450 | Casting On Door Lock Operating Mechanism; Front Brake Caliper Casting; Gearbox Lid Casting |
| mitte | air-conditioning-heating | gearbox | 3.22 | 0.615 | 449 | 2 key transmitter; 3 key transmitter; 9-3SS 03′ on – Remote Key Fob/Transmitter – Genuine |
| mbt | air-conditioning-heating | merchandise | 13.0 | 0.867 | 448 | Ambtn Polo North Face, 2; Ambtn Polo North Face, 3; Ambtn Polo North Face, L |
| baffle | safety-systems | body-exterior | 2.32 | 0.5 | 446 | Air Baffle; Air Baffle Plate; Air Cleaner Baffle |
| selector | hybrid-ev | gearbox | 8.58 | 0.722 | 441 | 3RG 23268 Selector- / Shift Rod; 3RG 23728 Gear selector cable Right, Left; ACKOJA A70-0594 Gear selector cable |
| פלס | fasteners | body-exterior | 3.95 | 0.603 | 440 | BAT TUAHER כיסוי פלסטי; BME SEHCA כסוי פלסטיק; CO DE EUOJ פלסטיק דופן |
| mechanism | air-conditioning-heating | interior-comfort | 4.04 | 0.713 | 426 | 9-3 Sports 4 Door & 5 Door 03′-07′ RH Window Regulator Mecha; Air Vent Box Door Opening Mechanism LH; Air Vent Box Door Opening Mechanism RH |
| רקסטון | audio-electronics | body-exterior | 1.71 | 0.255 | 420 | SsangYong Rexton - אגזוז קד'-רקסטון G4; SsangYong Rexton - אום גלגל-רקסטון; SsangYong Rexton - אום מסרק הגה-רקסטון |
| defrost | air-conditioning-heating | body-exterior | 2.15 | 0.544 | 415 | Actuator Defroster Flaps C644021; Air Conditioning Defrost Servo Motor; Arm Defroster Door |
| טיבו | interior-comfort | body-exterior | 1.9 | 0.279 | 415 | RH 'כיסוי נגד רטיבות דלת אח; SsangYong Korando - ערכת ניפוח-קורנדו-טיבולי; SsangYong Korando - צנור קד' לרגש לחץ דיפ'-טיבולי/ קור'' II |
| glv | fluids | body-exterior | 1.44 | 0.317 | 412 | 6 Mil Nit Glv M; 8 M Ltx Glvs Xl; Cls 00 Rbr Insul Glv |
| מדידה | gearbox | brakes | 1.75 | 0.438 | 408 | דיסקית מדידה Z46; טבעת מדידה ל ABS אופן קדמי; טבעת מדידה ל ABS בציר אח |
| pawl | air-conditioning-heating | clutch-drivetrain | 1.47 | 0.238 | 401 | clamp pawl shaft; Clamp Pawl Shaft; Genuine Discovery 3, 4 and Range Rover Sport Handbrake Adjus |
| mana | air-conditioning-heating | electrical | 1.31 | 0.32 | 400 | AIC 70901 Control Unit, engine management; AUTLOG AS4152 RPM Sensor, engine management; AUTLOG AS4218 Crankshaft sensor |
| לדוושת | air-conditioning-heating | brakes | 2.44 | 0.485 | 395 | Ball Head Mount 359900; BRACKET; BUMPER |
| אמצעי | lighting | body-exterior | 1.5 | 0.357 | 394 | Armrest in lode; BAG - ROOF PANEL STOWAGE; CABLE-PARKBRAKE |
| psa | brakes | engine | 3.45 | 0.54 | 392 | CEPSA 548362277 Automatic transmission fluid ATF III, Capaci; CEPSA 548372273 Automatic transmission fluid ATF III, Capaci; CHEMPIOIL CH8991-1 Power steering fluid DIN 51524-3, ISO 730 |
| פורטה | fasteners | body-exterior | 2.05 | 0.261 | 392 | Auxiliary Jack; Black Out Tape; Brkt Rr Beam Lwr Mtg |
| חידת | lighting | electrical | 4.86 | 0.578 | 388 | BODY CONTROL MODULE; Central control box; CONTROL ASSY |
| bague | gearbox | body-exterior | 1.8 | 0.375 | 387 | BAGUE 501 תותב בית גיר; BAGUE IMED חצי טבעת למנוע; BAGUE IME שגם לשסתומים |
| handset | body-exterior | audio-electronics | 1.12 | 0.333 | 383 | Cellular Phone Handset 841020; Cordless Handset For Telephone Module; Exchange Handset 841029 |
| tele | body-exterior | suspension-steering | 1.13 | 0.452 | 383 | ABAKUS 232-01-033 Shock absorber Top eye, Bottom eye, Front ; ABAKUS 232-02-005 Shock absorber Top pin, Bottom eye, Rear A; ABAKUS 232-02-008 Shock absorber Top eye, Bottom eye, Rear A |
| טורק | interior-comfort | engine | 2.1 | 0.326 | 380 | '16 1.5L אום לטורק פוקוס; Automatic Transmission Torque Converter; Body screw w |
| טיבול | safety-systems | body-exterior | 1.78 | 0.268 | 377 | SsangYong Korando - חצי מנוע-קורנדו II/ טורס/ טיבולי; SsangYong Korando - מחזיר שמן גל ארכובה-קורנדו II/ טיבולי; SsangYong Korando - ערכת ניפוח-קורנדו-טיבולי |
| tandem | fuel-air | brakes | 2.5 | 0.526 | 376 | Bracket Assembly Tandem Motor Cylinder; Cylassy Tandem Ma; Cyl Tandem Master |
| spg | interior-comfort | engine | 1.0 | 0.22 | 375 | 95 Spg Cat Conv Kit; A C Install Manl Spg; Badge Spg Ltd |
| למשקוף | interior-comfort | body-exterior | 10.36 | 0.814 | 374 | CRV 2007 קליפס למשקוף; door applique; Door weather |
| coupling | clutch-drivetrain | suspension-steering | 1.17 | 0.306 | 372 | 3RG 50214 Mounting, stabilizer coupling rod Front axle both ; ABAKUS 053-028-091 Rocker cover breather pipe with quick cou; AIC 74235 AWD pump, all-wheel-drive coupling |
| soft | merchandise | fuel-air | 3.0 | 0.368 | 372 | 6 Disc Cd Changer Software Application; 900 Convertible Soft Tonneau/Boot Cover: various colours; ACKOJA A38-81-0002 Throttle body Control Unit/Software must  |
| עוצר | filters | body-exterior | 28.67 | 0.846 | 370 | accessory drive belt tensioner assembly; BUMPER; BUMPER ASY - DOOR |
| bus | body-exterior | suspension-steering | 5.87 | 0.489 | 369 | 900 79′-93′ Alternator mounting bracket bush kit; ABAKUS 014-013-0001 Mercedes Sprinter 2t Minibus fan clutch; ABE C0G048ABE Nissan Interstar Minibus brake shoes |
| מהלכים | gearbox | interior-comfort | 1.43 | 0.269 | 365 | 1.4 MNU תיבת מהלכים מושלמת; 24297371 צמת חוטים לתיבת מהלכים חובה להזמין; 4X2 סט אטמים לתיבת מהלכים ידנית |
| בכונס | filters | body-exterior | 2.25 | 0.346 | 364 | HYUNDAI 86545AANA0; HYUNDAI 86545KL000; HYUNDAI 86545N9GA0 |
| לחזית | suspension-steering | body-exterior | 19.83 | 0.881 | 363 | AF UAERRA פח רוחב לחזית; LAC ELLIRG גריל לחזית; LAC ELLIRG פס קישוט לחזית |
| ואקום | interior-comfort | fuel-air | 1.48 | 0.327 | 361 | 3.0 צינור ואקום דיזל; #350 שסתום ואקום חש'למזגן; Booster Grommet |
| exchanger | air-conditioning-heating | cooling | 1.23 | 0.322 | 357 | 1 Set Pipes For Heat Exchanger; 3RG 82252 Heater hose, heat exchanger; AJUSA 16031600 Gasket, heat exchanger |
| spyder | accessories | brakes | 1.11 | 0.147 | 357 | 192996 133206 left rear brake disc MEDIDA 5 X 105 310MM MASE; 21 Inch Rs Spyder Design Summer Wheel And Tire; 21 Inch Rs Spyder Design Winter Wheel And Tire |
| שוגפ | clutch-drivetrain | electrical | 1.5 | 0.247 | 355 | bumper cover bracket; GENESIS 64900AR800; GENESIS 64900T1600 |
| outrigger | exhaust | body-exterior | 1.49 | 0.535 | 353 | BRACKET,OUTRIGGER,TYPE 1; fender outrigger; Fender Outrigger |
| guarnizione | belts-chains | interior-comfort | 27.0 | 0.964 | 351 | GUARNIZIONE ABBELL; GUARNIZIONE TENUTA |
| tenuta | belts-chains | interior-comfort | inf | 1.0 | 351 | GUARNIZIONE TENUTA |
| הגבהה | accessories | air-conditioning-heating | 1.57 | 0.355 | 349 | 65-8.8(בורג לכידון(הגבהה; F900GS הגבהה לכידון; Kit handlebaode |
| combo | body-exterior | electrical | 1.4 | 0.236 | 348 | 2 Lead Wire Combo W; 2x Front Brake Discs Vented 236mm for Vauxhall Corsa I 1993-; ABAKUS 442-1194LMLDEM2 OPEL Combo MPV (X19) headlights |
| חוט | belts-chains | electrical | 9.82 | 0.721 | 348 | 4JK1 צמת חוטים למנוע; 4X4 צמת חוטים למנוע משלב; BAT NODROC חוט למדף אחורי |
| למצתים | belts-chains | engine | 3.0 | 0.75 | 348 | EGATOPAC ןגמ B גומי למצתים; OEM Part - גומיה למצתים; OEM Part - סט חוטים למצתים |
| לגשר | audio-electronics | suspension-steering | 1.25 | 0.3 | 345 | 571X21 SIV בורג לגשר; BOLT; BOLT AND WASHER |
| reveal | hybrid-ev | body-exterior | 9.99 | 0.891 | 344 | back glass reveal molding; Back Glass Reveal Molding; Back Glass Reveal Molding Clip |
| לשקע | fuel-air | body-exterior | 1.45 | 0.449 | 341 | 12V כיסוי לשקע; 22 REIRTE ניצרה לשקע חשמל; AUTO FR SPRING BELT |
| קונטרה | fasteners | gearbox | 5.92 | 0.772 | 341 | BEARING ASY - COUNTERSHAF; GEAR-C/SHAFT; MIRP ERBRA קונטרה גיר |
| מתז | suspension-steering | wipers-washers | 2.81 | 0.437 | 334 | 0 כיסוי למתז קד'שמ'בפגוש; *90/87 צינור למיכל מתזים; Cap |
| קורנד | safety-systems | body-exterior | 1.53 | 0.201 | 331 | SsangYong Korando - אגזוז מר'-קורנדו II; SsangYong Korando - אגזוז קד'-קורנדו C; SsangYong Korando - אטם בית מסנן EGR-קורנדו II |
| pliers | interior-comfort | body-exterior | 11.53 | 0.689 | 318 | Circlip Pliers; Circlip Pliers 140 Mm Vas 5493; Circlip Pliers – 4 Applications |
| taycan | accessories | air-conditioning-heating | 1.73 | 0.251 | 318 | 2022 Taycan Qsg; 2022 Taycan Warranty And Customer Inform; 2023 Taycan Warranty And Customer Inform |
| men | merchandise | body-exterior | 1.22 | 0.318 | 316 | 20 Inch Taycan Tequipment Design Winter; ABAKUS 042-36-381 Bumper reinforcement Left Front; Adjustment Part |
| maxgear | gearbox | suspension-steering | 1.71 | 0.191 | 313 | Audi Cabriolet 8g7 b4 Top strut mount MAXGEAR 72-0313; BRAKE HOSE LINE PIPE FRONT LEFT MAXGEAR 52-0362 FOR CHEVROLE; BRAKE HOSE LINE PIPE FRONT LEFT RIGHT MAXGEAR 52-0294 FOR DA |
| לנפ | clutch-drivetrain | filters | 1.57 | 0.286 | 313 | COVER,HOLE; C פין לקונבאר לנפה; Hole Cover |
| keys | merchandise | engine | 3.71 | 0.739 | 311 | Bezel Assy Keyset; Cb Keys Manual Rav4; Cylinder And Keys |
| אורות | lighting | electrical | 3.35 | 0.56 | 293 | 0) 9.08-מ E-90 מתג אורות; CLOCK SPRING; COMMUTATEUR מתג אורות |
| conducting | air-conditioning-heating | fluids | 3.57 | 0.735 | 291 | 2k Heat Conducting Paste; Oil Conducting Housi; Oil Conducting Housing |
| transceiver | electrical | gearbox | 7.85 | 0.723 | 289 | Bracket For Transceiver For Telephone; Cable Transceiver; cover transceiver |
| אוט | merchandise | gearbox | 2.71 | 0.476 | 289 | 1LT שמן גיר TUTELA AS8 (דיילי אוטו מלא); 4L שמן הגה גיר אוטו DEX III תקן TES IV; (4X4) 'מיסב לגל הינע אוט |
| לתה | filters | engine | 1.01 | 0.176 | 288 | 13403; 2EGAYARBME בית מצמד לתהא; 3EGAYARBME בית מצמד לתהא |
| closed | service-general | engine | 1.03 | 0.394 | 287 | ABAKUS 142-01-509 Timing chain kit with chain tensioner, Sim; ABAKUS 142-01-516 Timing chain kit with camshaft gear, Simpl; ABAKUS 142-01-518 Timing chain kit with chain tensioner, Sim |
| grid | service-general | body-exterior | 3.96 | 0.585 | 287 | 14FT GRID CASSETTE; 14' TIPPING GRID RETAINER LH; 4" GRID BAR RETAINER |
| משלב | body-exterior | gearbox | 3.38 | 0.434 | 286 | 4X4 כפתור משלב; 4X4 צמת חוטים למנוע משלב; EHCRAM EXA משלב הילוך אח |
| אביזרים | clutch-drivetrain | belts-chains | 1.38 | 0.28 | 283 | ADDIT.SOCKET; HYUNDAI 56111N9DB0NNB; HYUNDAI 56111S0000NNB |
| adj | lighting | interior-comfort | 1.62 | 0.322 | 281 | ABAKUS 135-03-009 Switch, mirror adjustment; A.B.S. K24900 Clutch Cable with manual adjustment; ACKOJA A32-0021 Camshaft Adjuster Intake Side |
| utility | body-exterior | suspension-steering | 1.14 | 0.177 | 280 | A-Pillar Mounting Kit For Utility-25 &amp; Utility-25 MAXX L; BLUE PRINT AD18CH450 Opel Corsa Classic windshield wipers; BLUE PRINT ADZ99126 Opel Corsa C Utility water pump |
| במע | fasteners | cooling | 1.38 | 0.227 | 279 | COVER - OPENING; HYUNDAI 285002MAF5; Leapmotor Part 000000009867606780 |
| אורך | clutch-drivetrain | body-exterior | 1.65 | 0.505 | 278 | # 4X4 תושבת מוט אורך אקו; (A) צינור בלם מתכת לאורך הרכב; (B) צינור בלם מתכת לאורך הרכב |
| spe | fluids | suspension-steering | 7.69 | 0.634 | 267 | 2016-2023 Toyota Tacoma 4wd 6 Inch Suspension Lift Kit 4 Inc; 555 SB-H022 Mazda CX-5 KF suspension ball joint; 5 Speed Gearbox Conversion With 0.63 ratio 5th Gear |
| בוכנות | brakes | engine | 4.09 | 0.652 | 267 | 13428B; 1.4 סט בוכנות למנוע; C18 NOTSI בוכנות |
| הידראולי | accessories | electrical | 1.52 | 0.315 | 262 | (11.S)שמן הידראולי; )47( יח' הידראולית ומחשב SBA; Abs Control Unit |
| diffe | gearbox | clutch-drivetrain | 6.28 | 0.804 | 260 | automatic transmission differential bearing; Automatic Transmission Differential Bearing; Automatic Transmission Differential Carrier |
| pass | body-exterior | wipers-washers | 3.48 | 0.352 | 258 | "AIR BAG, Passenger, Export, [CGW]"; Air Bag Passenger Presence Module; Arm Windshield Wiper Passenger Side |
| בוקסות | electrical | suspension-steering | 4.4 | 0.458 | 258 | BAGUE בוקסות לסטרטר; DE BOUCHON בוקסות לגיר; DE EUGA עמ F בוקסות לבולם |
| איחסון | audio-electronics | interior-comfort | 2.17 | 0.426 | 253 | Cargo Box; REAR STORAGE ELECT DUCT; RENAULT RE849290602R |
| למניפולד | fasteners | lighting | 1.88 | 0.36 | 253 | UC NOHCUO פקק למניפולד; אומים למניפולד; אום למניפולד |
| טנדר | interior-comfort | body-exterior | 1.81 | 0.274 | 251 | 4X4 מצבר 19 טנדרים; 4X4 מצבר דגם 19 טנדרים; BULKHEAD ASSEMBLY |
| לחשוק | cooling | fasteners | 10.5 | 0.875 | 251 | Center Cap; אום חיצוני לחשוק אופן; אום לחשוק אופן |
| מוד | interior-comfort | body-exterior | 1.24 | 0.337 | 251 | 0GS תושבת לעמוד אח' את"ן; Airbag controde; Airbag contror |
| נייד | body-exterior | electrical | 4.71 | 0.434 | 246 | CHARGER, BATTERY-WIR; Charging devode; MODULE - WIRELESS CHARGIN |
| nitrile | interior-comfort | engine | 17.42 | 0.791 | 243 | 3RG 84219 Inlet manifold gasket NBR (nitrile butadiene rubbe; AJUSA 11089500 Rocker cover gasket NBR (nitrile butadiene ru; AJUSA 14070800 Oil sump gasket NBR (nitrile butadiene rubber |
| siren | audio-electronics | electrical | 1.42 | 0.5 | 243 | Alarm Siren With Level Sensor; Alternative Power Siren C659087; anti theft alarm siren |
| tap | service-general | engine | 2.52 | 0.524 | 243 | 147 Panorama Tappet FAI AutoParts BFS100; 3m Automotive Attachment Tap 832520; 780 Tappet FAI AutoParts BFS101S |
| תעלת | fasteners | air-conditioning-heating | 1.08 | 0.246 | 240 | AIR CHANNEL; Air Channel Right 517110; air duct |
| speedster | accessories | brakes | 1.79 | 0.224 | 239 | ABAKUS 141-03-017 OPEL Speedster (E01) wheel bearing; A.B.S. 200001 PORSCHE Taycan Sport Turismo wheel bearing; A.B.S. 35422 Porsche 911 964 Convertible brake pads |
| copper | service-general | engine | 2.13 | 0.419 | 235 | 3RG 80099 Sump plug washer Copper; AIC 52648 Sump plug washer Copper; Air Vent Copper |
| heating | cooling | engine | 4.57 | 0.744 | 235 | 14 Hatchback Ignition coil ERA 880111A; 21 K48 Ignition coil HELLA 5DA 358 000-851; 21 K48 Ignition coil RIDEX 689C0087 |
| vintage | safety-systems | body-exterior | 3.91 | 0.494 | 233 | 73 Lancer, Vintage Black; 74 Lancer, Vintage Black; 75 Lancer, Vintage Black |
| anniversary | cooling | merchandise | 2.25 | 0.474 | 230 | 40TH Anniversary Beanie; 40TH Anniversary Trucke; 40TH Anniversary T-Shir |
| shir | cooling | merchandise | 4.7 | 0.738 | 230 | 24 Heures T-shirt L; 25y Carrera Gt T Shirt Black; 25y Carrera Gt T Shirt White |
| rev | hybrid-ev | lighting | 2.55 | 0.48 | 229 | 780 Indicator bulb BOSCH 1 987 302 811; A.B.S. 31134 TOYOTA Previa / Estima II (XR30) abs sensor; A.B.S. 36009 Volkswagen 412 Variant brake pads |
| למצית | gearbox | electrical | 1.18 | 0.333 | 228 | 12/24 HD שקע למצית+כיסוי; 12 Volt Accessory Power Outlet Housing; RING,ILLUMINATION |
| לריטרדר | gearbox | electrical | 2.33 | 0.389 | 228 | בולם הידראולי לריטרדר; יחידת בקרה לריטרדר; מכסה אחורי לריטרדר גיר 16S2221 |
| טמפ | air-conditioning-heating | electrical | 8.55 | 0.776 | 226 | # 12 EXP חיישן טמפ' למזגן; 1 חיישן טמפרטורה באגזוז; Coolant Temp Sensor |
| ספורט | air-conditioning-heating | body-exterior | 3.48 | 0.374 | 224 | 1000XR M מושב ספורט; ARM ASY - FRONT SUSPENSIO; CABLE ASSY |
| משפך | air-conditioning-heating | fuel-air | 1.1 | 0.256 | 223 | Absorber funor; Filler Tube; FORD 2050651 Fuel filler pipe |
| מרצדס | wipers-washers | body-exterior | 3.58 | 0.633 | 222 | בורג לכוכב מרצדס; בורג לסמל מרצדס בגריל קד; בסיס לכוכב מרצדס בגריל קד P15/772 |
| tel | air-conditioning-heating | suspension-steering | 1.1 | 0.272 | 219 | 20LT חביונית TUTELA ATF 6K; ABAKUS 232-01-033 Shock absorber Top eye, Bottom eye, Front ; ABAKUS 232-02-012 Shock absorber Top eye, Bottom eye, Rear A |
| התאוצה | fuel-air | lighting | 2.5 | 0.625 | 218 | בולם גומי זרוע דוושת התאוצה; בולם גומי מכבל התאוצה לקיר אש; דוושת התאוצה |
| מסמרה | filters | body-exterior | 2.14 | 0.359 | 217 | 815 מסמרה; Bumper Cover Fastener; Bumper Cover Grommet |
| שגם | interior-comfort | engine | 3.0 | 0.508 | 217 | 43 שגם גל ארכובה םם; BAGUE IME שגם לשסתומים; Key Crankshaft |
| counter | hybrid-ev | gearbox | 1.44 | 0.26 | 216 | 2BA Raised Countersunk Screw - Slotted oval head, 2"; Bearing Counter Shaft; Blind Rivet Nut Countersunk 889523 |
| supporting | engine | suspension-steering | 1.57 | 0.457 | 213 | 3RG 45742 Supporting Ring, suspension strut bearing; Actuator Supporting Plate C000090; Audi 80 B1 Supporting Ring, suspension strut bearing ORIGINA |
| ncs | interior-comfort | wheels-bearings | 2.67 | 0.308 | 212 | Cinturato P7 A/S Ao Ncs; Mult Pncs; Pirelli Pzero E Ncs Elt Str 369041 |
| קלצ | engine | air-conditioning-heating | 4.0 | 0.56 | 208 | כבל קלצ; מיכל קלצ; מיסב לקלצ |
| premier | wheels-bearings | brakes | 1.59 | 0.282 | 207 | AE CAM1012 DAEWOO Lacetti Premiere Saloon (J300) camshaft; AE CAM1013 DAEWOO Lacetti Premiere Saloon (J300) camshaft; AE CAM1020 DAEWOO Lacetti Premiere Saloon (J300) camshaft |
| sdm | body-exterior | electrical | 18.5 | 0.888 | 207 | Asdm; Asdm Adapter; FORD Transit Connect Mk3 MPV Track rod end SIDEM SDM914N0005 |
| ארכ | interior-comfort | engine | 6.3 | 0.682 | 207 | #10 בורג לגלגלת גל ארכובה; 1.9 גל ארכובה; 2# מיסב לגל ארכובה |
| קייל | fasteners | engine | 7.0 | 0.7 | 206 | '20 FOC קייל גל ארכובה; D EMAC מקייל; EXP קייל לשסתום |
| למסגרת | clutch-drivetrain | body-exterior | 12.35 | 0.824 | 203 | 57 קליפס למסגרת פנס ראשי; D ESSILUOC קדר למסגרת דלת; D TNATNOM פחית למסגרת דלת |
| משמנת | engine | fluids | 1.3 | 0.419 | 203 | 750GS משמנת אוטומטית לשר; Kit, chain-o; Oil Sprayingode |
| פלנש | body-exterior | cooling | 3.5 | 0.483 | 202 | P ED SPROC פלנש מים; SAIC MG RX5 Water pump outlet pipe – TEE -1.5T; SUOS ELAC אטם לפלנש מאייד |
| balljoin | audio-electronics | suspension-steering | 3.62 | 0.731 | 201 | 900 94′-95′ Control Arm, incl Balljoint LH (A) – see descrip; 900 94′-95′ Control Arm, incl Balljoint RH (A) – see descrip; 900 94′-98′ & 9-3 98′-03′ BallJoint Boot Kit |
| etr | audio-electronics | engine | 1.23 | 0.216 | 201 | 9-3 00′-03′ Manual Petrol – DO88 Intercooler Pressure Pipe &; 9-3 98′-03′ all 4 cyl Petrol models – Top Radiator to Thermo; 9-3SS 03′-11′ 4 cyl Petrol models (B207) – Genuine Saab Oil  |
| maxi | body-exterior | suspension-steering | 1.49 | 0.225 | 201 | 2x BRAKE DISC 0 986 478 833 FOR NISSAN AVENIR STAGEA CEFIRO/; 555 SB-4882 Nissan Maxima A32 suspension ball joint; ABAKUS 120-08-233 Nissan Maxima A32 lambda sensor |
| גחון | wheels-bearings | body-exterior | 7.14 | 0.725 | 199 | 20 תפס מגן גחון אקספלורר; 4X2 מגן גחון; (4X4)מגן גחון |
| זמן | belts-chains | electrical | 1.33 | 0.435 | 199 | EDNAMMOC שעון זמן; ERTNOM שעון זמן; SsangYong Rexton שעון זמן-רקס 07-12 |
| מיגון | wheels-bearings | body-exterior | 1.46 | 0.275 | 199 | KIT - BODY CONVERSION; KIT - ROCK RAIL; Leapmotor Part 000000000082211995 |
| baulk | service-general | interior-comfort | 2.2 | 0.55 | 198 | BAULK RING; Baulk Ring for 3rd/4th and 5th/Reverse for R380 - Defender, ; Baulk Ring for R380 Gearbox - 1st/2nd/3rd/4th/5th Gear |
| esp | suspension-steering | interior-comfort | 1.75 | 0.242 | 198 | 1 Set Foot Mats Espr; 2x ABS Sensor and ABS Ring Set Front Axle Speed Controller f; 2x BRAKE DISC 09.9608.24 FOR DAEWOO CIELO LANOS/SENS NEXIA E |
| rest | suspension-steering | interior-comfort | 6.04 | 0.755 | 195 | A.B.S. 51711 Subaru Forester SH clutch master cylinder; AMiO 02194 Subaru Forester SH tailpipe; Armrest |
| אקס | gearbox | body-exterior | 1.81 | 0.271 | 194 | # '15 רדיו דיסק אקס; # 19 'מחשב מולטימדיה אקס; #20 'גומי גשם פנ' שמ' אקס |
| posipan | service-general | fasteners | 5.54 | 0.818 | 193 | Posipan Screw - #10-32; Posipan Screw - 1/4"; Posipan Screw - M4 |
| con | air-conditioning-heating | electrical | 1.2 | 0.233 | 192 | 2x BRAKE DISC 19-2795 FOR INFINITI M37 FX/SUV EX QX50 Q70L G; 9-3 98′-03′ Inner Control Arm (Stanchion) LH; 9-3SS 2.8 V6 all Turbo X (XWD) – Single Mass Conversion Kit  |
| toothed | engine | electrical | 3.38 | 0.511 | 191 | 4 Toothed Belts Long For Fat 829019; Alternator Belt - Toothed style; Cover For Toothed Belt |
| triangle | air-conditioning-heating | body-exterior | 1.78 | 0.405 | 191 | Cover For Warning Triangle M 889527; Door Finisher Triangle LH; Front door triangle panel |
| מייסבים | body-exterior | suspension-steering | 1.68 | 0.593 | 191 | BOLT; BOLT AND WASHER ASY - HEX; בית מייסבים שמאל |
| פלדה | gearbox | wheels-bearings | 1.83 | 0.539 | 190 | C רשת פלדה מנגן מ. }0451{ 0451 נקב 21 מ" מ; HYUNDAI 52960DR200; HYUNDAI 52960HF100 |
| tex | interior-comfort | brakes | 1.93 | 0.342 | 189 | 1 Set Footmats Textile; 1 Set Footmats Textile Black Carmine R; 1 Set Footmats Textile Black Chalk |
| גתמ | clutch-drivetrain | electrical | 1.5 | 0.25 | 189 | bulb-8499910320; CONTACT גתמ PC נעילת דלתות; GENESIS 93571AR001VCS |
| שימסים | audio-electronics | lighting | 1.6 | 0.222 | 188 | מע שימסים לדיפ אח צדדי J82015; מע' שימסים לפיניון סרן קד' +אח' - JK/ZG/; סט שימסים |
| bottle | merchandise | cooling | 2.51 | 0.325 | 187 | 12 Ton Bottle Jack; 20 Ton Bottle Jack; Active Anti-Roll Bar Coolant Reservoir Expansion Bottle - Ra |
| vcs | clutch-drivetrain | fluids | 12.0 | 0.824 | 187 | GENESIS 93310CU500VCS; GENESIS 93571AR001VCS; GENESIS 96110CU010VCS |
| supt | exhaust | body-exterior | 4.4 | 0.458 | 186 | BRACKET-FR BPR CORNER SUPT LH; BRACKET-FR BPR CORNER SUPT RH; Bracket Supt Eng Mtg |
| השהייה | clutch-drivetrain | electrical | 1.6 | 0.348 | 186 | Press in buffer block; יח' השהייה למצתי להט; לבדוק חלופי לפי שילדה ממסר השהייה |
| שימשה | wipers-washers | body-exterior | 1.73 | 0.356 | 186 | Gutter Clip; RUN CHANNEL(R),GLASS; Window Channel |
| viscous | body-exterior | clutch-drivetrain | 1.43 | 0.37 | 183 | 2.7 TDV6 Viscous Fan - For Discovery 3 &amp; 4 and Range Rov; Coupling Assy Viscous; Coupling Viscous |
| לסוללה | hybrid-ev | electrical | 5.14 | 0.573 | 183 | 740E 'מקרן לסוללה על' שמ; AIR SUPPLY UNIT; Battery Cover Gasket |
| jeans | accessories | engine | 1.5 | 0.414 | 180 | Jeans; Jeans jacketode; Jeans Kreuzbode |
| drier | merchandise | air-conditioning-heating | 684.75 | 0.995 | 179 | ABAKUS 017-021-0005 AC receiver drier; ABAKUS 019-021-0001 AC receiver drier; ABAKUS 035-021-0001 AC receiver drier |
| sled | merchandise | lighting | 1.5 | 0.5 | 179 | mopar parking brake sled assembly; Shoe Fr Sled; Sled Assy Frt Lh |
| זמזם | gearbox | electrical | 4.92 | 0.621 | 179 | 97DB זמזם נסיעה לאחור; ALARM,BACK UP 51097DB 12V; Anti Theft Alarm Siren |
| tech | service-general | engine | 1.0 | 0.175 | 178 | 20 Inch 5 Double Spoke Tech Black Diamond; 408 Saloon Top strut mount Magnum Technology A7P016MT; 504 GRD Pickup Holding Clip Set, body Hogert Technik HT8G551 |
| דיזה | air-conditioning-heating | wipers-washers | 2.26 | 0.53 | 178 | AL RUELCIG דיזה; AL RUELCIG דיזה למתיז מים; AL RUELCIG דיזה לשפריצר |
| enyaq | electrical | suspension-steering | 1.94 | 0.256 | 177 | ABAKUS 053-025-0016 SKODA Enyaq IV SUV engine thermostat; A.B.S. 231205 SKODA Elroq (PYL) track rod end; AISIN BPVG-7030 SKODA Enyaq iV Coupe brake pads |
| planet | service-general | engine | 1.27 | 0.228 | 175 | Carrier Assy A T Planet; Carrier Assy Planet; Carrier Assy Planetr |
| user | accessories | body-exterior | 3.3 | 0.495 | 175 | Aero Kit Rear Diffuser; Aero Kit Rear Diffuser Brilliant Black; Air Diffuser Rear Bumper |
| ארגז | air-conditioning-heating | body-exterior | 1.54 | 0.434 | 174 | 00GS כיסוי אח' שמ' לארגז; 00GS תושבת פלס' לארגז צד; 0GTL 'תיק פנימי לארגז שמ |
| urea | fluids | fuel-air | 1.94 | 0.309 | 173 | 2015 DACIA SANDERO STEPWAY LAUREATE TCE Radiator Expansion T; Bracket Urea Pump C; Bracket Urea Tank |
| כול | fasteners | body-exterior | 1.17 | 0.177 | 173 | 21 כולל קליפסים ודסקיות D; 21 כולל קליפסים ודסקיות ח NLS D; C מיסב כולל בית לדריב שפט שופלים |
| ימינפ | clutch-drivetrain | lighting | 1.4 | 0.333 | 169 | back up light assembly; GENESIS 82610AR000VCA; GENESIS 82610T6000VCA |
| דלתית | fuel-air | body-exterior | 2.87 | 0.588 | 168 | 4X2 דלתית מיכל דלק; 4X4 דלתית מיכל דלק; 5D דלתית דלק |
| dok | merchandise | suspension-steering | 1.33 | 0.299 | 167 | 1x Brake Caliper Front Left for DACIA DOKKER, DUSTER, LOGAN,; 1x Brake Caliper Front Right for DACIA DOKKER, DUSTER, LOGAN; 2x BRAKE DISC 24.0124-0253.1 FOR DACIA JOGGER DOKKER/Pickup/ |
| למצוף | merchandise | fuel-air | 3.09 | 0.548 | 167 | JOINT אטם למצוף דלק; OEM Part - גומיה למצוף מיכל דלק; OEM Part - מחזיק למצוף דלק |
| attaching | fasteners | suspension-steering | 1.11 | 0.328 | 166 | Attaching; Attaching Arm; Attaching Plate |
| אומים | body-exterior | fasteners | 9.36 | 0.708 | 166 | BME UORC אומים אגזוז; CX5 סט אומים לוו גרירה; EGAC UORC אומים |
| סימון | body-exterior | lighting | 1.55 | 0.456 | 166 | AT סימון לגיר; AT פלסטיק סימון גיר; LAMP |
| staples | belts-chains | fasteners | 1.33 | 0.421 | 165 | BLUE PRINT ADBP420073 Brake pad set with acoustic wear warni; BLUE PRINT ADC44272 MITSUBISHI L200 / Triton IV Pickup (KA, ; Chisel Point Staples |
| adaptor | electrical | wipers-washers | 9.69 | 0.633 | 160 | Adaptor; ADAPTOR; Adaptor Assy Fuel Pu |
| רוטור | merchandise | engine | 2.36 | 0.456 | 160 | CONTROL; COVER; DAEH-ROTO רוטור למפלג |
| ריקון | interior-comfort | fluids | 1.61 | 0.404 | 159 | 22 דיסקית לפקק ריקון שמן; Differential Cover Plug; Engine Oil Drain Plug |
| noitcelloc | fasteners | electrical | 1.5 | 0.25 | 157 | NOITCELLOC מע סתמים תה; NOITCELLOC מערכת טבעות; NOITCELLOC מערכת תותבים |
| מרכז | fasteners | body-exterior | 2.16 | 0.407 | 155 | adapter for ode; Air duct wit; Armrest |
| שרש | audio-electronics | belts-chains | 15.0 | 0.887 | 155 | 135ORCX112 CHAIN EC2003; 3.7 שרשרת תיזמון; *-65 מוביל לשרשרת תיזמון |
| synchronizr | gearbox | interior-comfort | inf | 1.0 | 154 | SYNCHRONIZR RING |
| int | fuel-air | suspension-steering | 1.12 | 0.19 | 153 | VAICO V30-50066 Mercedes Sprinter 4,6-t Van water pump; Daewoo Tacuma Rezzo KLAU Tailgate strut QUINTON HAZELL QTS82; DAEWOO Tico Hatchback Timing Belt BLUE PRINT ADT37501 |
| פרופיל | wipers-washers | filters | 1.68 | 0.531 | 151 | 59.1 X 465.0 גומי עם פרופיל; 852.1 X 553.0 גומי עם פרופיל לתיבת ניפוי; DE LIFORP פרופיל דלת קדמי |
| overhaul | service-general | engine | 1.08 | 0.366 | 150 | automatic transmission overhaul kit; Automatic Transmission Overhaul Kit; Automatic Transmission Overhaul Service Seal Kit |
| בדיקה | gearbox | engine | 5.0 | 0.577 | 149 | CAP WITH WAR **ריקול בדיקה 50 רכבים**; Leapmotor Part 000000002000040988; כבל למכשיר בדיקה / ריקול |
| לחיישני | gearbox | electrical | 7.23 | 0.791 | 149 | 2 צמת חוטים לחיישני חניה; 40 צמת חוטים לחיישני חניה; Adapter for ode |
| משקפיים | wipers-washers | body-exterior | 3.37 | 0.57 | 147 | Apron Extension; Box O H Console; Console Overhead |
| hic | accessories | engine | 1.0 | 0.193 | 145 | 1 Set Trim Strips For Vehicle Interior A; 1 Set Trim Strips For Vehicle Interior E; 1 Set Trim Strips For Vehicle Interior P |
| louver | body-exterior | air-conditioning-heating | 1.03 | 0.468 | 144 | Bonnet Louvers Pair; Case Louver Rh; cowl grille louver |
| למפריד | audio-electronics | engine | 1.5 | 0.321 | 144 | RESERVOIR; אטם למפריד שמן; אטם למפריד שמן במנוע |
| harrier | electrical | suspension-steering | 1.69 | 0.3 | 143 | ABAKUS 141-01-303 Highlander XU40 wheel bearing; A.B.S. 16277 TOYOTA Harrier I (XU10) brake discs; A.B.S. 201557 TOYOTA Harrier II (XU30) wheel bearing |
| thread | service-general | engine | 4.14 | 0.525 | 143 | 3RG 83002 Oil sump plug Spanner Size: 21, Thread Size: M10 x; 3RG 83050 Wheel Bolt Thread Size: M12 x 1,5; 3RG 83533 Oil sump plug Thread Size: M14x1,5 |
| potenza | wheels-bearings | electrical | 6.86 | 0.84 | 142 | 205 50 R17 91w Potenza S001 369041; 215 45r17 Potenza Re040; 225 40 R19 89y Potenza S001 369041 |
| term | suspension-steering | electrical | 4.21 | 0.42 | 142 | 670037769 – Single Aftermarket Maserati Front Brake Disc Lev; 9-5 99′-02′ LH plastic mirror surround fm ch no. X3070507 – ; AIC 55726 Intermediate Shaft Front Axle, Right, for manual t |
| טכוגרף | brakes | electrical | 5.0 | 0.625 | 142 | TACHOGRAPH ASSY; חיוט טכוגרף; חיישן למונה מהירות [טכוגרף] |
| מאריך | accessories | body-exterior | 1.56 | 0.283 | 142 | BOX USB כבל מאריך; BUMPER - EXT.; BUMPER - EXTENSION |
| rtv | body-exterior | air-conditioning-heating | 1.36 | 0.333 | 141 | 16c Black Rtv Silico; 6br Blue Rtv Silicon; AVA COOLING SYSTEMS RTV173 Air conditioning evaporator |
| לשלט | hybrid-ev | electrical | 1.62 | 0.356 | 141 | Battery; Battery Pack 612110; Case Transmitter |
| alphard | electrical | suspension-steering | 1.53 | 0.213 | 137 | A.B.S. 18118 Alphard AH20 brake discs; A.B.S. 18408 TOYOTA RAV4 V (XA50) brake discs; A.B.S. 31133 Alphard AH10 abs sensor |
| piping | fuel-air | lighting | 2.03 | 0.519 | 137 | Cap, A/C Piping; clamp piping; Clamp Piping |
| quilted | merchandise | air-conditioning-heating | 1.05 | 0.312 | 137 | Bonnet Insulation Kit - Quilted; Bonnet Insul Quilted; B Quilted Gi |
| דחיסה | fasteners | air-conditioning-heating | 1.5 | 0.395 | 137 | SsangYong Actyon - צנור דחיסה מזגן-אקטיון/ אקטיון ספ' II; SsangYong Actyon - צנור דחיסה מזגן-אק'פ; SsangYong Korando - צנור דחיסה-קורנדו II |
| ils | air-conditioning-heating | suspension-steering | 2.1 | 0.306 | 136 | A.B.S. 36488 BMW E28 brake pads; BILSTEIN 12-244676 Top strut mount Front Axle; BILSTEIN 19-289052 Renault Koleos 2 shock absorber |
| astra | interior-comfort | suspension-steering | 1.56 | 0.176 | 134 | 1X Original GM Chevrolet Brake Pad Set Daewoo Opel Astra Fro; 2x DELPHI Brake Discs Front Fits Bedford Astra Van Daewoo La; 555 SR-N870 OPEL Astra K Box Body / Hatchback (B16) tie rod |
| prop | body-exterior | clutch-drivetrain | 1.6 | 0.379 | 134 | ACKOJA A52-70-0036 Ignition lead set EPDM (ethylene propylen; AP XENERGY X90039 Propshaft, axle drive Front; ASHIKA GOM-H229 Propshaft centre bearing Centre |
| סנוכ | clutch-drivetrain | air-conditioning-heating | 1.25 | 0.263 | 133 | air duct; engine cooling fan shroud; GENESIS 25380T1000 |
| לתומך | wipers-washers | body-exterior | 2.45 | 0.367 | 132 | ABS בורג לתומך מנוע; ABS תפס לתומך מנוע; BOLT |
| פיה | filters | fuel-air | 1.38 | 0.239 | 130 | Cover f fillor; ER ED EBUT פיה לצנור דלק; headlight washer cover |
| משדר | interior-comfort | electrical | 2.31 | 0.521 | 128 | Key Blanking Pic; Key Insert; RCZ משדר מצמד |
| שוגפב | clutch-drivetrain | electrical | 1.25 | 0.278 | 128 | GENESIS 86521AR800; GENESIS 86521T6500; GENESIS 86577AR100CR |
| יונית | fuel-air | gearbox | 1.21 | 0.258 | 127 | 20lt TRANSMISSION ZCS 160 80W90 GL-4; 20LT חביונית TUTELA ATF 6K; 20LT חביונית שמן מנוע 15W40 E7 |
| לסטליטים | clutch-drivetrain | fasteners | 1.71 | 0.364 | 127 | OEM Part - טבעת מירווח לסטליטים; Z14 גיר לסטליטים; Z9 גיר לסטליטים |
| ניפוח | fuel-air | wheels-bearings | 4.31 | 0.418 | 127 | ADJUSTER; Air Bag R Curtain; AIR BAG(R),CURTAIN |
| אוטומטי | air-conditioning-heating | gearbox | 3.45 | 0.581 | 126 | 3.0 בית משאבת שמן אוטומטי; 4X4 קפיץ אח' ד.ק. אוטומטי; 4 מ.שמן אח' לגיר אוטומטי 2 ר |
| שאר | merchandise | engine | 2.4 | 0.414 | 126 | bulb-9098113047; bulb-9098113075; GENESIS 223212R100 |
| bel | safety-systems | belts-chains | 2.7 | 0.511 | 125 | 9-3 Sports Diesel 08′-11′ 1.9 Twin Turbo (TTiD) Timing Belt ; accessory drive belt; Accessory Drive Belt Tensioner Assembly |
| אספקת | filters | fuel-air | 1.68 | 0.495 | 125 | # 18 F150 צינור אספקת דלק; Distribution Box; # F26 מודול אספקת מתח |
| במיכל | filters | fuel-air | 3.24 | 0.5 | 125 | FUEL FILTER; Gasket; GASKET |
| מסמכים | merchandise | interior-comfort | 1.12 | 0.346 | 125 | F56 MI תיק מסמכים; OEM Part - דלתית תא מסמכים; OEM Part - פנס לתאורת תא מסמכים |
| סופורט | fasteners | engine | 4.8 | 0.667 | 125 | HIF UORCE אום לסופורט; VB TROPPUS סופורט למנוע; בורג לסופורט מנוע |
| עצר | fasteners | body-exterior | 2.02 | 0.46 | 125 | Additional sode; BUMPER; BUMPER ASY - DOOR |
| מוח | suspension-steering | gearbox | 36.05 | 0.928 | 124 | Adapter 249900; AT מוח לגיר; AT מוח לגיר שסתום |
| cinnamon | merchandise | interior-comfort | 1.19 | 0.233 | 123 | 1 Set Foot Mats Cinnamon Brown; Actuating Lever Cinnamon Brown; Adhesive Carpet Cinnamon Brown |
| קניסטר | merchandise | fuel-air | 1.19 | 0.279 | 123 | Activated Chode; Bracket Canister; BRACKET,CANISTER |
| leve | hybrid-ev | electrical | 1.01 | 0.14 | 122 | 3RG 13242 Tensioner Lever, v-ribbed belt; 3RG 13702 Tensioner Lever, v-ribbed belt; 4 Dr Vntvsr Level B |
| הזיזים | engine | fuel-air | 1.15 | 0.238 | 121 | RENAULT 20X42X; RENAULT JOINT405X; X24X02 TLR מיסב גל הזיזים |
| שירות | service-general | body-exterior | 1.5 | 0.308 | 121 | Auxiliary Outlet; Connector; FRT BUMPER ASM - SERVICE |
| sup | fluids | body-exterior | 1.79 | 0.277 | 120 | 315 30r21 Gy Eag F1 Supspt R; 928 Supp Ii; Aggregate Support Bracket C519590 |
| מפירוק | fasteners | engine | 2.0 | 0.32 | 120 | 169A000; INCOMPLETE ENGINE 263A2000; SsangYong Musso - תיבת העברה מפירוק-מוסו ספורט II |
| גוזו | gearbox | wheels-bearings | 2.73 | 0.318 | 119 | 1X01NOJUOG גוזון גיר; 1X8 NOJUOG גוזון מניפולד; 4/3NOLEMA גוזון מסנן שמן |
| כליפת | interior-comfort | body-exterior | 3.67 | 0.733 | 118 | כליפת גג; כליפת גג אח IR7+D03; כליפת גג מרכזית IR7 |
| לצמה | body-exterior | electrical | 1.92 | 0.581 | 118 | ADAPTER CABLE CONN. HVTS; CLIP ETROP תקע לצמה; Holder ode |
| afe | fluids | safety-systems | 8.75 | 0.709 | 117 | AGRAFE קליפס; AGRAFE קליפס לצינורות; AGRAFE תפס |
| follower | hybrid-ev | engine | 105.12 | 0.981 | 117 | ARM - FOLLOWER; Cam Follower; Cam Follower 119900 |
| uch | wheels-bearings | engine | 1.18 | 0.188 | 117 | BOUCHON בורג; CITROËN steering rack Metalcaucho 50405; Copper Touch Up Paint |
| bod | fluids | body-exterior | 3.87 | 0.566 | 115 | Abdeckung Abode; A.B.S. 35182 KIA Ceed III Box Body / Hatchback (CD) brake pa; AC Rolcar 01.4451 PEUGEOT 307 SW Box Body / Estate (3E_, 3H_ |
| retaini | service-general | body-exterior | 1.7 | 0.343 | 115 | 2x Genuine Apec Brake Disc Retaining Screws For Audi; 2X REAR BRAKE DISC RETAINING SCREW FIT: RANGE ROVER SPORT 13; A.B.S. 96167 Spring Retaining Pin, brake shoe |
| voc | air-conditioning-heating | brakes | 1.77 | 0.5 | 115 | 10 Voc Brake A; 10 Voc Brake And Pa; 10 Voc Brake P |
| חולץ | merchandise | suspension-steering | 1.0 | 0.191 | 115 | SsangYong SsangYong - חולץ קפיץ שסתומים-רק; SsangYong SsangYong - סט חולץ מרססים; בורג חולץ לבול גוינט |
| מירכוז | gearbox | body-exterior | 1.2 | 0.188 | 115 | DOWEL; DOWEL - PIN; אום מירכוז למנגנון חלון |
| sprag | electrical | gearbox | 4.45 | 0.664 | 114 | automatic transmission sprag assembly; Automatic Transmission Sprag Assembly; mopar front sun gear sprag clutch |
| אקטיון | air-conditioning-heating | body-exterior | 1.69 | 0.214 | 113 | SsangYong Actyon - אגן שמן תי'ה-אקטיון; SsangYong Actyon - אום גלגל קד'-אקטיון; SsangYong Actyon - אופן מגנזיום-אקטיון |
| stub | brakes | suspension-steering | 1.24 | 0.455 | 111 | 3 8 Stub Twist; 3RG 83027 Nut, stub axle; 3RG 83028 Nut, stub axle |
| ישאר | clutch-drivetrain | engine | 2.4 | 0.462 | 111 | bulb-9098113047; bulb-9098113075; GENESIS 92102AR530 |
| עינית | audio-electronics | fuel-air | 1.0 | 0.379 | 111 | COVER - FRT BUMPER TOWING EYE; KIT-JET; RR BUMPER TOWING EYE COVER |
| נפץ | merchandise | safety-systems | 1.36 | 0.366 | 110 | Actor, left; Actor, left or; Actor, rear ode |
| מבדד | fuel-air | body-exterior | 1.5 | 0.391 | 109 | INSULATOR-PANEL; גומי מבדד במראה ימ; גומי מבדד למשולש מראה חצ.ימ. |
| הרקב | clutch-drivetrain | electrical | 2.0 | 0.333 | 108 | black out tape; BOITIER הרקב LE לגיר אוטמט; GENESIS 391502CCA0 |
| זחל | body-exterior | fasteners | 1.0 | 0.375 | 107 | 0-17 בורג לזחל; 9-210 אום לבורג זחל; אום לבורג חוליה לזחל באגר סאני |
| טיפול | service-general | fluids | 1.7 | 0.37 | 107 | 1LT טיפול מנוע QMI; Repair Kit F Oil Cooler Oil A11400; טיפול 0002 כולל שמנים מסננים כללי ומסנני אוראה=126 |
| hitch | accessories | body-exterior | 3.67 | 0.571 | 106 | Affiliated Yakima Reg Ridgeback 4 Hitch Mount Bike Rack; Ag Chr W Chr Hitch Plug Nissan Logo; AKS DASIS 048103N Fan, radiator Ø: 295mm, without radiator f |
| vac | fuel-air | air-conditioning-heating | 1.84 | 0.381 | 106 | 9-5 01′-10′ RHD B205, B235 MANUAL Brake Servo Vacuum Hose – ; 9-5 99′-10′ RHD Automatic Brake Servo Vacuum Hose – Alternat; Air Conditioning Vacuum Supply Harness |
| מודולטור | interior-comfort | engine | 2.15 | 0.664 | 105 | 07 - 05 לג'נד מודולטור; 1 מודולטור; 2 מודולטור |
| exp | gearbox | cooling | 1.23 | 0.313 | 104 | 06040 BRAKE SHOE SET KIT REAR LPR FOR MG MG ZS,MG ZR,EXPRESS; ABAKUS 003-026-002 Coolant expansion tank with coolant level; ABAKUS 017-026-003 Coolant expansion tank without lid |
| felt | electrical | interior-comfort | 1.03 | 0.339 | 104 | Acoustic Felt Rear Left 514810; Acoustic Felt Rear Left 529900; Acoustic Felt Rear Right 514810 |
| קדם | audio-electronics | filters | 1.9 | 0.322 | 104 | Leapmotor Part 000000000032850227; OEM Part - מקדם הצתה; OEM בורג M414 לממסר קדם טעינה |
| camber | suspension-steering | fasteners | 6.02 | 0.678 | 103 | Alignment Camber Adjusting Eccentric; alignment camber adjusting eccentric bolt; Alignment Camber Adjusting Eccentric Front |
| not | service-general | engine | 2.42 | 0.405 | 101 | 3d Flexi Note B; 9-3 98′-02′ & NG900 94′-98′ LH door mirror glass (Not Wide A; 9-3 98′-02′ (Not Aero & Viggen) Replacement Rear Spring |
| aluminium | service-general | cooling | 3.89 | 0.545 | 100 | ABAKUS 004-017-0001 BMW E38 engine radiator; ABAKUS 004-017-0047 Engine radiator Aluminium, Brazed coolin; ABAKUS 009-017-0016 Engine radiator Aluminium, Manual Transm |
| supplementary | accessories | safety-systems | 1.0 | 0.208 | 100 | 9-3 & 9-5 98′-03′ (see description) Supplementary Crankcase ; Adapter Airbag Supplementary Tester Vas; Airbag Supplementary Tester Vas 5056d |
| דיסקה | fasteners | brakes | 3.65 | 0.492 | 100 | BOLT; BOLT AND WASHER ASY - HEX; G -דיסקה |
| timer | belts-chains | body-exterior | 1.18 | 0.25 | 99 | Bracket Relay Dr L Timer; Bracket Roomlamp Timer; Bracket Room Lamp Timer |
| אמב | fasteners | body-exterior | 1.77 | 0.298 | 97 | BRACKET, CAST GRABBER, 4.5IN; BRACKET,OUTRIGGER,TYPE 1; BULB,35W,HALOGEN ASFIR P\N908001 |
| applied | service-general | engine | 1.25 | 0.357 | 96 | Abs Assy Not Applied; Not Applied |
| lta | filters | electrical | 4.73 | 0.766 | 96 | AIC 56206 Alternator voltage regulator Voltage: 14V; AIC 57294 Alternator voltage regulator; AISIN ESMMB-7001 Starter motor Starter Output: 2kW, Voltage: |
| sucti | fuel-air | cooling | 1.07 | 0.299 | 96 | ABAKUS 102-00-003 Renault Master II Minibus oil pump; A C Refrigerant Suction; A C Refrigerant Suction Hose |
| פלגים | brakes | engine | 4.67 | 0.636 | 96 | 5 EHCATTA תפס חוטי פלגים; KRAPS-EBU מוביל לפלגים; PORSCHE 94810593300 |
| component | electrical | exhaust | 2.03 | 0.423 | 95 | Air Filter Component (Qq0.8Front Part); Box cover components; brake system components |
| based | service-general | fasteners | 116.29 | 0.973 | 94 | Alcohol Based Hand S; Bolt Washer Based; bolt washer based h |
| ohc | air-conditioning-heating | engine | 1.92 | 0.343 | 94 | 1M NOHCUO מכסה פקק; 22 NOHCUO פקק לראש מנוע; 2x BRAKE DISC 1032368 FOR JEEP CHEROKEE/LIBERTY R 425 DOHC 2 |
| הסכמ | clutch-drivetrain | body-exterior | 1.29 | 0.188 | 94 | BOITIER הסכמ OC פלסטי לחוט; BOUCHON הסכמ ER מיכל שמן ב; CAPOT הסכמ HCAC בית פיוזים |
| סיוויק | fasteners | body-exterior | 2.97 | 0.386 | 94 | 1.8 5/D כרית לבולם קדמי סיוויק**; אטם סיוויק 1.8 4/D; איגניטור סיוויק 2009 ES |
| butnup | hybrid-ev | merchandise | 1.29 | 0.429 | 93 | Easy Care Butnup Shirt,; Wmns 3/4 Sleeve Butnup,; Wmns Easy Care Butnup, B |
| wmns | hybrid-ev | merchandise | 3.57 | 0.575 | 93 | Wmns 3/4 Sleeve Butnup,; Wmns Ambitn Polo Ogio, 2; Wmns Ambitn Polo Ogio, 3 |
| התקנה | fasteners | engine | 1.05 | 0.224 | 93 | AGR small paode; Air Valve; ARNAV-USB-5VH |
| נעילת | interior-comfort | body-exterior | 1.58 | 0.408 | 93 | ACTUATOR ASY; BEZEL,LOCK; CABLE ASSY |
| דלתות | interior-comfort | body-exterior | 5.89 | 0.659 | 92 | AGRAFE תפס מגן דלתות; Button, centode; Door Latch Service K |
| scoop | service-general | body-exterior | 6.0 | 0.642 | 90 | Air Scoop Assy Lh; Air Scoop Bracket LH; Air Scoop Bracket RH |
| למצב | fasteners | electrical | 72.06 | 0.947 | 90 | 2.2 כבל למצבר; BATTERY MANAGEMENT SYSTEM; Battery sensor |
| מיקרו | fasteners | electrical | 1.59 | 0.331 | 90 | 6521 RP מיקרופיש מסטר; #8 מכלל וילון, מיקרופייבר; F54 מיקרופון |
| ellednor | merchandise | fasteners | 1.0 | 0.25 | 89 | 1 ELLEDNOR דיסקית לציר; 1 ELLEDNOR טבעת לגיר; 4 ELLEDNOR שיבה מרווח |
| face | interior-comfort | body-exterior | 1.16 | 0.184 | 89 | 3m Wheel Surface Cleaner Prep 2000 16oz; ABAKUS 103-06-012 FORD Taunus 26M XL Coupe (53F) wiper motor; ACAUTO AC-05DN20 Ford S-Max Mk1 ac compressor clutch |
| סיליקון | service-general | engine | 1.36 | 0.273 | 89 | ADHESIVE; Engine Sealant; Gasket |
| justy | gearbox | suspension-steering | 1.35 | 0.214 | 88 | ABAKUS 037-017-0094-B Subaru Justy G3X engine radiator; ABAKUS 142-01-515 Subaru Justy G3X timing chain; ABAKUS 218-1117L-LD-EM Subaru Justy 2 headlights |
| tools | service-general | electrical | 1.91 | 0.316 | 88 | ABAKUS 120-09-027 Camshaft Adjuster Intake Side, Requires sp; ABAKUS 142-01-509 Timing chain kit with chain tensioner, Sim; ABAKUS 142-01-534 Timing chain kit Simplex, Closed chain, Re |
| אנט | wheels-bearings | audio-electronics | 6.16 | 0.662 | 87 | AERIAL ASY; AERIAL ASY - GPS; AERIAL WITHOUT CABLE |
| פרינג | wipers-washers | body-exterior | 1.48 | 0.426 | 87 | 00 RR(YN2E)'פרינג זנב שמ; 1250GS 'פרינג שמ; (750GS)מדבקה לפרינג ימין |
| clutc | brakes | clutch-drivetrain | 10.16 | 0.789 | 86 | 3RG 13649 Alternator clutch pulley Ø: 61mm, Width: 37,2mm; 3RG 22210 Release Fork, clutch; 3RG 22211 Release Fork, clutch |
| הידוק | gearbox | body-exterior | 1.48 | 0.272 | 86 | BOLT; BOLT JACK SET; Cable Tie 611332 |
| girls | hybrid-ev | body-exterior | 3.73 | 0.644 | 83 | Girls Back P; Girls Cap Pi; Girls Cap Pl |
| למזלג | suspension-steering | gearbox | 1.08 | 0.283 | 83 | BEARING - BALL; ELLIPUO פין למזלג; ELLIUOD בוקסה למזלג |
| identification | engine | air-conditioning-heating | 1.88 | 0.455 | 82 | Bracket Gear Identification C239900; Chassis Identification Plate; Child Car Seat With Identification |
| לפין | suspension-steering | brakes | 1.25 | 0.227 | 81 | clip pin hold; EXP אבטחה לפין בוכנה; Lock Ring |
| רוניצ | clutch-drivetrain | cooling | 1.86 | 0.197 | 81 | A EBUT רוניצ UAE גומי למים; A EBUT רוניצ UAE מים לחימו; A EBUT רוניצ UAE מים למצנן |
| workshop | service-general | engine | 1.5 | 0.255 | 79 | 340 Saloon Workshop seat cover WALSER 47024; Cdrom Parts/Workshop; CDROM Parts/Workshop |
| בנג | gearbox | fasteners | 3.0 | 0.545 | 79 | BOLT; BOLT - BANJO; BOLT,CONNECTOR |
| קופסאת | gearbox | electrical | 6.24 | 0.728 | 78 | GENESIS 91950CU100; GENESIS 91950CU800; HYUNDAI 31420DR300 |
| santa | gearbox | suspension-steering | 2.09 | 0.24 | 77 | ABAKUS 120-01-043 Hyundai Santa FE DM parking sensors; ABAKUS 1554G04 Hyundai Santa FE DM glass for wing mirror; A.B.S. 200923 Grandeur HG wheel bearing |
| אלט | merchandise | electrical | 22.82 | 0.899 | 77 | # '15 'אלטרנטור טרנ; 2.0 מכסה פולי אלט אקובלו; Alternator |
| מינוס | merchandise | electrical | 10.4 | 0.839 | 77 | BATTERY CABLode; Battery cablor; Cable Assembly |
| מתברג | gearbox | fasteners | 1.17 | 0.2 | 77 | Lower Molding Screw; Screw plug; SCREW, TAPPING |
| handbo | accessories | merchandise | 1.54 | 0.481 | 76 | CD HANDBOOK LANC-MUSA; CD HANDBOOK N.PANDA; Daimler Handbook |
| efarga | wipers-washers | body-exterior | 1.0 | 0.4 | 75 | IAM EFARGA קליפס; IAM EFARGA קפיץ; IAM EFARGA תפס |
| mage | fuel-air | body-exterior | 1.97 | 0.251 | 75 | 2POIUlock body; 9-3SS Combi/Wagon 2003 on Clear Tail lamp Left Hand LHD DAMA; A.B.S. 240544 Kia Magentis 2 tie rod |
| pus | wipers-washers | body-exterior | 1.03 | 0.22 | 75 | 1 4 Dot Push In U; 200TDI Valve and Push Rod Kit - Land Rover Defender, Range R; 3 8 Dot Push In U |
| marketing | merchandise | air-conditioning-heating | 1.0 | 0.5 | 74 | Marketing Sign; Porsche Lifestyle Retail Marketing Packa; Product Marketing |
| starex | gearbox | wheels-bearings | 2.8 | 0.397 | 74 | ABAKUS 140-01-390 Hyundai H1 Starex power steering pump; ABAKUS 221-1115L-LD-E Hyundai H1 Starex headlights; ABAKUS 221-1119L-LD-EM Hyundai H1 Starex headlights |
| troppu | electrical | body-exterior | 1.31 | 0.354 | 73 | AF TROPPUS תושבת פח חזית; AP TROPPUS תושבת דוושה; AR TROPPUS כיסוי לרדיו |
| allstreet | gearbox | suspension-steering | 1.37 | 0.19 | 72 | ABAKUS 053-017-0093 AUDI A1 Allstreet (GB) engine radiator; ABAKUS 138-01-061 AUDI A1 Allstreet (GB) water pump; ABAKUS 141-04-171 AUDI A1 Allstreet (GB) cv joint |
| אוחז | engine | electrical | 1.12 | 0.214 | 72 | Plug In Connection Bracket T46000; RUBBER DOOR HOLDER MALE; SET CASE HOLDER |
| cont | hybrid-ev | suspension-steering | 1.16 | 0.375 | 71 | 9-5 98′-10′ all models – Rear Upper Cross Stay/Control Arm; ABAKUS 136-01-003 Lighting control module both sides; ACKOJA A38-1166 Nissan Primera Traveller W10 control arm |
| מחיצה | gearbox | body-exterior | 3.0 | 0.467 | 71 | BULKHEAD ASSEMBLY; F56 מחיצה בתא סוללות; RENAULT RE792767297R |
| סנפל | clutch-drivetrain | lighting | 1.0 | 0.227 | 71 | bulb-9098113047; bulb-9098113075; GENESIS 92183CU000 |
| inspection | service-general | body-exterior | 1.12 | 0.48 | 70 | Cover-inspection; Cover-Inspection; Cover Inspection Ho |
| למתיזים | air-conditioning-heating | cooling | 2.0 | 0.304 | 70 | 22845370+מיכל מים למתיזים; G20 משאבת למתיזים; NLS 'מנוע למתיזים קד |
| קולר | gearbox | cooling | 1.58 | 0.4 | 68 | 218D F46 קולר שמן תיבת הילוכים; BOLT; Bracket for ode |
| peridot | filters | interior-comfort | 3.29 | 0.561 | 65 | Armrest Black Peridot; Backrest Cover Black Peridot; Centre Console Black Peridot |
| ass | accessories | body-exterior | 2.08 | 0.333 | 64 | 9-3SS 03′-12′ CV – Lowered Sports Chassis Aero Spring Kit (4; ABAKUS 2809G01 Wing mirror glass Left, Heated; Actuator Assy Brake |
| לממיר | interior-comfort | exhaust | 1.81 | 0.388 | 64 | 8M 'אום לממיר קד; BOLT; Bracket |
| מנתיב | audio-electronics | electrical | 5.5 | 0.714 | 64 | # G30 חיישן סטיה מנתיב; * G60 חיישן סטייה מנתיב; G60 חיישן סטייה מנתיב |
| סטייה | audio-electronics | electrical | 6.22 | 0.737 | 64 | Blind Spot Detection System Warning Sensor Bracket; BRACKET; * G60 חיישן סטייה מנתיב |
| luc | merchandise | electrical | 1.83 | 0.27 | 63 | 9.5mm Straight Lucar Connector Pkt of 50; AIC 56335 E36 reluctor ring; Bulb 12v 2w Ba7. Lucas |
| ריווא | clutch-drivetrain | engine | 1.33 | 0.222 | 63 | air duct; dashboard air vent; GENESIS 25380T1000 |
| buttons | interior-comfort | body-exterior | 23.0 | 0.852 | 62 | 1 Set Attachment Buttons For Foot Mat B; 1 Set Attachment Buttons For Foot Mat P; 1 Set Attachment Buttons For Foot Mat S |
| inductive | audio-electronics | engine | 1.4 | 0.538 | 62 | ABAKUS 120-05-036 Camshaft position sensor Inductive Sensor; ABAKUS 120-05-037 Camshaft position sensor Inductive Sensor; ABAKUS 120-05-038 Camshaft position sensor Inductive Sensor |
| רפידת | fasteners | brakes | 10.25 | 0.774 | 62 | Front brake pad; Front Brake Pad Service Kit; FRONT BRAKE PAD SERVICE KIT |
| kurz | service-general | air-conditioning-heating | 1.8 | 0.512 | 61 | Kurzanleitung Cayenne Niederlandisch; Kurz Betriebsanleitung 911; Kurz Betriebsanleitung 911 Carrera |
| reference | service-general | air-conditioning-heating | 7.54 | 0.737 | 61 | 2017 French 911 Turbo Quick Reference Gu; 911 Turbo Quick Reference Guide Us My17; Asst Reference Card 861011 |
| אינץ | merchandise | wipers-washers | 1.06 | 0.282 | 61 | 19 Inch RIM; 21 Inch RIM; LR/JAG Part - בולם קדמי מסדרת PRO SPORT מתוצרת TERRAFIRMA המ |
| לירג | clutch-drivetrain | body-exterior | 1.33 | 0.381 | 61 | GENESIS 86350AR110TRI; GENESIS 86350G9AC0; GENESIS 86350T1AD0CR |
| סנכרון | fasteners | gearbox | 5.0 | 0.526 | 61 | 2 LIBOM ENOC סנכרון הילוך; CONE; CONE AND GEAR ASY - 5TH S |
| stahl | exhaust | cooling | 1.69 | 0.289 | 60 | SW-Stahl 01426L Vacuum Pump; SW-Stahl 01435L-3 Spindle, brake caliper piston reset tool; SW-Stahl 01470-11 Repair Kit, brake caliper at brake caliper |
| compl | accessories | body-exterior | 2.1 | 0.413 | 59 | 1 Set Complete Summer Wheels; 1 Set Complete Summer Wheels Black; 1 Set Complete Summer Wheels Japan |
| value | service-general | belts-chains | 1.36 | 0.197 | 59 | Battery Value G 35; BGA 6PK1873 Serpentine belt; Can Liners Value Pac |
| צובר | gearbox | fluids | 1.54 | 0.377 | 58 | 1LT בצובר 15W40 CJ4 VDS4 פזפרפקט -פז; 1LT בצובר 15W40 CJ4 -פז; 1LT צובר 15W40 CJ4 VDS4 פזפרפקט -פז |
| fastening | fasteners | exhaust | 1.08 | 0.193 | 57 | ABAKUS 141-04-009 CV joint kit with grease lubrication, with; ABAKUS 141-04-018 CV joint kit with grease lubrication, with; ABAKUS 141-04-019 CV joint kit with grease lubrication, with |
| order | service-general | air-conditioning-heating | 3.28 | 0.498 | 57 | 8 Speed ZF Automatic Gearbox for 3.0 TDV6 - Range Rover Spor; ASTA S-HCTK Compression Pressure Recorder; Blue Coolant 6 Cs 10 Pallet Orders |
| אוני | fasteners | engine | 1.33 | 0.32 | 57 | GAL-OR SWIA13150A; HYUNDAI 243562M000; RAM-HOL-UN7BU |
| בורד | audio-electronics | interior-comfort | 5.92 | 0.755 | 56 | Clip for scrode; COVER ASY; COVER,HOLE-CLOCK |
| לדש | audio-electronics | interior-comfort | 3.86 | 0.643 | 56 | Clip for scrode; CORD,SHORT; COVER ASY |
| evaporative | fuel-air | air-conditioning-heating | 5.65 | 0.765 | 55 | Evaporative Emissions Canister; Evaporative Emissions System; Evaporative Emissions System Leak Detection |
| interm | engine | clutch-drivetrain | 1.12 | 0.219 | 55 | 9000 94′-98′ intermediate exhaust pipe for front mount CAT; AIC 55726 Intermediate Shaft Front Axle, Right, for manual t; AJUSA 15004400 Shaft Seal, intermediate shaft |
| picnic | interior-comfort | air-conditioning-heating | 1.33 | 0.275 | 55 | A.B.S. 16552 Toyota Picnic MPV brake discs; A.B.S. 210555 Toyota Picnic MPV control arm; A.B.S. 31775 Toyota Picnic MPV abs sensor |
| sencom | service-general | lighting | 2.35 | 0.637 | 55 | Citroen Xantia Estate Connecting cable, wing mirror SenCom T; febi bilstein 10006 SKODA Felicia Cube Van (6U5) camshaft; febi bilstein 10006 T4 Transporter camshaft |
| דמה | wheels-bearings | body-exterior | 1.71 | 0.375 | 55 | F34 סט פקקי דמה למגן קד; I3 רמקול מדמה נסיעה; Trim Cover |
| למפעיל | hybrid-ev | cooling | 1.17 | 0.156 | 55 | Screw for acor; אטם למפעיל RCV; אטם למפעיל בורר הילוכים |
| פרו | wheels-bearings | filters | 1.06 | 0.273 | 55 | 0743TN RBH007K9K ספרות; 1002 ES A 1918 TN C ספרות; 45 פרו רייס M מגפי |
| awfl | hybrid-ev | air-conditioning-heating | 1.5 | 0.25 | 54 | 4run Awfl Trd Pro No; Ava Awflm 4p; es awd awfl 4p brn |
| ravh | hybrid-ev | body-exterior | 1.8 | 0.346 | 54 | Ravh Abs Shk Rr Wdln; Ravh Awcm Blk Woodla; Ravh Bdge Gry Woodla |
| woo | hybrid-ev | brakes | 1.51 | 0.248 | 54 | 1997 DAEWOO LEGANZA ABS CONTROL UNIT 0265218521; 1997 DAEWOO NEXIA ABS Pump/Modulator; 1x Brake Pads Front for Daewoo Nubira 97- |
| repeater | body-exterior | lighting | 9.0 | 0.813 | 53 | 9000 85′-90′ 5D Clear Corner clusters/DRL’s (Pair) & Side Re; Bulb Holder for Oval Style Side Repeaters - Fits Defender 19; Defender Clear Side Repeater from XA159807 Onwards - Fits De |
| לטלפון | accessories | electrical | 1.56 | 0.28 | 53 | ADAPTER,TELEPHONE; MODULE; Multiband teode |
| מסילות | clutch-drivetrain | body-exterior | 1.82 | 0.513 | 53 | BOLT; #F350 ער' מסילות לחלון גג; *F350 ער' מסילות לחלון גג |
| einparts | accessories | lighting | 2.03 | 0.524 | 52 | Citroen C Crosser Enterprise Fog Light EINPARTS DUOLIGHT DL2; EINPARTS 402O Daytime running lights; EINPARTS 402O PLUS Daytime running lights |
| למעצור | cooling | body-exterior | 7.5 | 0.75 | 52 | Bolt; BT+MC בורג למעצור הגה; HOUSING |
| ניטים | brakes | body-exterior | 2.33 | 0.368 | 52 | 4 ED TEVI ניטים; Blind rivet ode; LEXUS 9026904051 |
| ריצ | merchandise | wipers-washers | 2.05 | 0.408 | 52 | 01-02 מיכל שפריצר אקורד אנגלי; 01-02 משאבת שפריצר אקורד אנגלי; 2010 CRV מיכל שפריצר |
| רפילק | merchandise | filters | 1.67 | 0.588 | 52 | bush rr cylinder sl; disc brake caliper; disc brake caliper bolt |
| handgrip | gearbox | body-exterior | 1.0 | 0.433 | 51 | Chrome Roof Handgrip Endcap; Handbrake Handgrip; Handgrip-par |
| par | gearbox | engine | 1.16 | 0.18 | 51 | 9000 1989 to 1998 w/ separate CAT (see descr) – Catalyst Typ; ABAKUS 120-01-030 Parking sensor black, grey, Ultrasonic Sen; ABAKUS 120-01-044 Parking sensor black, Ultrasonic Sensor, F |
| snr | suspension-steering | belts-chains | 1.08 | 0.315 | 51 | Audi A4 B9 Saloon Wheel bearing kit SNR R157.43; CITROËN C3 Origin III damping SNR KB659.02; FIAT 124 Spider (348) Water pump and timing belt kit SNR KDP |
| לטלטלים | air-conditioning-heating | wheels-bearings | 4.15 | 0.614 | 51 | Engine Connecting Rod Bearing Set; METAL SET,CONN.ROD; בוקסה לטלטלים |
| corteco | service-general | engine | 5.22 | 0.58 | 50 | CITROËN XM I Hatchback (Y3) Track rod end CORTECO 49398525; CORTECO 005522S Sump plug washer Aluminium; CORTECO 005590S Sump plug washer Copper |
| לצנור | fuel-air | electrical | 1.33 | 0.2 | 50 | DE TUOBME פין לצנור בלם; DROCCAR תבשות CE לצנור אגז; EPMOP םותסש CRIC לצנור מים |
| מגבהה | brakes | wipers-washers | 9.0 | 0.857 | 50 | 4X4 מגבהה; NLS מגבהה; OEM Part - ידית למגבהה |
| עודפי | fuel-air | cooling | 1.21 | 0.462 | 50 | (A) צינורית עודפים; (B) צינורית עודפים; Coolant hose |
| bicycle | accessories | suspension-steering | 3.12 | 0.588 | 49 | Aluminum Bicycle Holder With Frame; ASHIKA ZSA05013 Tailgate strut both sides, for vehicles with; Bicycle |
| sottozero | wheels-bearings | interior-comfort | 3.71 | 0.753 | 49 | Pirelli W210 Sottozero 369042; Pirelli W210 Sottozero Ii Mo 369042; Pirelli W240 Sottozero Ii Mo 369042 |
| תינוציח | clutch-drivetrain | lighting | 1.33 | 0.267 | 49 | exterior door handle; GENESIS 82650CU040UYH; GENESIS 83660CU040PH3 |
| לראדיאטור | exhaust | body-exterior | 1.5 | 0.441 | 48 | HYUNDAI 86350L5680; HYUNDAI 86351NI100CA; HYUNDAI 863A0L5690 |
| lugg | hybrid-ev | body-exterior | 1.72 | 0.397 | 47 | 6 Series Embroidered Luggage Compartment Mat; All Weather Luggage Compartment Mat; bar luggage door hi |
| outlande | accessories | suspension-steering | 1.92 | 0.224 | 47 | 555 SR-B010 MITSUBISHI Outlander III Van (GF, GG) tie rod; ABAKUS 123-00-167 Mitsubishi Outlander 2 cylinder head; A.B.S. 17433 MITSUBISHI Outlander III Van (GF, GG) brake dis |
| owners | accessories | engine | 1.2 | 0.4 | 47 | MANUAL-OWNERS; OWNERS HANDBOOK; Owners Hbook French |
| transverse | clutch-drivetrain | fluids | 1.41 | 0.347 | 47 | ASTA A-05001 Engine Transverse Strut; BUSHINGTRANSVERSE LINKREAR; Front Transverse Element 9100000235 |
| arde | clutch-drivetrain | wheels-bearings | 2.06 | 0.597 | 46 | 2 1 Hi Def Hardener Fast 759801; AE V91144 Inlet valve Valve stem tip hardened; AE V91209 Inlet valve Valve stem tip hardened |
| differen | audio-electronics | clutch-drivetrain | 8.18 | 0.855 | 46 | automatic transmission differential bearing; Automatic Transmission Differential Bearing; Automatic Transmission Differential Carrier |
| surface | brakes | wheels-bearings | 1.25 | 0.339 | 46 | 2k Primer Surfacer 759801; 2 Surface Prep Medium Mar 759011; 3m Wheel Surface Cleaner Prep 2000 16oz |
| אמבטיה | interior-comfort | body-exterior | 1.0 | 0.244 | 46 | LEXUS PZ434K2304PJ; LH פח חזוק לאמבטיה; RH פח חזוק לאמבטיה |
| pants | merchandise | air-conditioning-heating | 1.06 | 0.285 | 45 | 911 Spirit 70 Track Pants Black; Bbmw Mms Sweat Pants Reg Cc 999999; Bmwbmw Mms Ess Pants Fleece 9999999 |
| drill | service-general | brakes | 1.6 | 0.436 | 44 | 1 2 Rev Drill; 262mm FRONT DRILLED GROOVED BRAKE DISCS FOR ROVER MG ZR ZS 2; 31 Piece Zebra Drill |
| ruehcel | fasteners | body-exterior | 21.0 | 0.913 | 44 | DE RUEHCEL גומיה חלון דלת; DE RUEHCEL גומיה לחלון; DE RUEHCEL גומי חלון |
| למולטימדיה | gearbox | audio-electronics | 27.25 | 0.916 | 44 | IOB מסך למולטימדיה; IOB מתגים למולטימדיה; OEM יחידת בקרת אנטנה למולטימדיה |
| למתז | clutch-drivetrain | wipers-washers | 1.47 | 0.375 | 44 | 0 כיסוי למתז קד'שמ'בפגוש; Bracket, was; Cap |
| resistor | electrical | air-conditioning-heating | 7.88 | 0.772 | 43 | ABAKUS 016-014-0004-R Fan, radiator Ø: 310mm, with electric ; ABAKUS 133-003-002 Blower motor resistor; ABAKUS 133-003-003 Blower motor resistor |
| רלי | gearbox | suspension-steering | 1.33 | 0.238 | 43 | 00 55ZR17 78W צמיג פירלי; 0 70 ZR17 58W צמיג פירלי; 120/70ZR17 צמיג פירלי |
| csk | service-general | engine | 1.26 | 0.51 | 42 | Csk.fillisteode; Csk Fillister Head Srew A13100b; Csk Fillister Head Srew A51140 |
| ling | clutch-drivetrain | suspension-steering | 1.02 | 0.217 | 42 | 780 Tappet FAI AutoParts BFS101S; ABAKUS 009-017-0070 Engine radiator Aluminium, Mechanically ; ABAKUS 212-11M5R-LD-EM Headlight HB3, H11, without bulb, wit |
| posi | service-general | engine | 1.21 | 0.38 | 42 | ABAKUS 120-05-053 Camshaft position sensor Hall Sensor; ABAKUS 120-05-078 Camshaft position sensor; ABAKUS 120-08-003 Throttle position sensor |
| hert | interior-comfort | engine | 1.41 | 0.212 | 41 | Besta Van Glow plug HERTH+BUSS JAKOPARTS J5713000; Besta Van Glow plug HERTH+BUSS JAKOPARTS J5713008; CITROËN BX Box Body / Hatchback (XB-_) Ignition lead set HER |
| לטרברסה | clutch-drivetrain | fasteners | 1.88 | 0.469 | 41 | ENIGNE-DA אום לטרברסה; ERREUQ פחית לטרברסה; בורג לטרברסה |
| מתלים | gearbox | electrical | 1.86 | 0.433 | 41 | 81 ער' שיפוץ מתלים ספורט; Axle Beam; AXLE,TORSION-REAR SU |
| רמן | interior-comfort | body-exterior | 2.67 | 0.421 | 41 | SsangYong Actyon - מצת-אקטיון/רקס/צ'רמן; SsangYong SsangYong כנף קד L-צ'רמן; SsangYong SsangYong - מוט קישור-צ'רמן |
| wartungsbroschure | service-general | air-conditioning-heating | 6.2 | 0.861 | 40 | 911 Garantie Und Wartungsbroschure A; Garantie Und Wartungsbroschure 911 Gt2; Garantie Und Wartungsbroschure 918 Spyde |
| לצילינדר | fasteners | body-exterior | 4.11 | 0.529 | 40 | 1 'צינור דלק לחץ גבוה לצילינדר מס; 5 צינור דלק לצילינדרים 4 ו; 6 'צינור דלק לצילינדר מס |
| רזרבי | merchandise | wheels-bearings | 16.5 | 0.84 | 40 | #6.7L ער'נעילת גלגל רזרבי; Board C Trunk; BOARD(C),TRUNK |
| רעשים | interior-comfort | body-exterior | 1.82 | 0.436 | 40 | Acoustic covode; Acoustic pluode; A 'ק. סט משתיק רעשים עלים אח |
| art | clutch-drivetrain | electrical | 1.22 | 0.242 | 39 | Alfa e-Parts AF01552 Nissan Pathfinder R50 abs sensor; Alfa e-Parts AF01826 Speed sensor; Alfa e-Parts AF02103 Air conditioning pressure switch |
| boxer | electrical | suspension-steering | 1.75 | 0.222 | 39 | ABAKUS 009-018-0001 Peugeot Boxer Minibus turbo intercooler; ABAKUS 140-01-220 Peugeot Boxer 230 Van power steering pump; ABAKUS 661-1122L-LD-EM Peugeot Boxer 230 Van headlights |
| nitril | interior-comfort | engine | 17.42 | 0.79 | 39 | 3RG 84219 Inlet manifold gasket NBR (nitrile butadiene rubbe; AJUSA 11089500 Rocker cover gasket NBR (nitrile butadiene ru; AJUSA 14070800 Oil sump gasket NBR (nitrile butadiene rubber |
| סניק | brakes | engine | 1.9 | 0.437 | 39 | A0028 סניק DC דיסק; A6728TN תקליטור סניק; II 1 סניק USIV ספרות דיסק |
| silicone | service-general | engine | 1.37 | 0.443 | 38 | 9-3SS/SC 03′ on 2.0 & 1.8 Turbo (B207) – DO88 Silicone Coola; AJUSA 15029800 Crankshaft seal transmission sided, FMQ (Fluo; AJUSA 75000400 Oil sump gasket Silicone, Capacity: 75ml, not |
| uesivorter | merchandise | body-exterior | 1.2 | 0.545 | 38 | UESIVORTER מראה; UESIVORTER מראה חיצונית; UESIVORTER מראה ימין |
| מתכוונת | merchandise | electrical | 10.0 | 0.833 | 38 | Footrest, ri; הארמ UESIVORTER י' מתכוונת; הארמ UESIVORTER ש' מתכוונת |
| עיגון | fasteners | body-exterior | 3.29 | 0.541 | 38 | GENESIS 86517AR100; GENESIS 86517AR800; GENESIS 86517T6700 |
| elring | service-general | engine | 4.81 | 0.686 | 37 | Audi Q7 4LB Rocker cover gasket ELRING 323.480; Brava Estate Oil sump gasket ELRING 671.100; City Z engine ELRING 743.660 |
| אומי | merchandise | fasteners | 9.36 | 0.689 | 37 | BME UORC אומים אגזוז; CX5 סט אומים לוו גרירה; EGAC UORC אומים |
| לאבפורטור | fasteners | engine | 3.0 | 0.49 | 37 | אטם גומי לאבפורטור; חיישן טמפ' לאבפורטור; חיישן טמפרטורה לאבפורטור |
| מוניטור | merchandise | engine | 4.5 | 0.643 | 37 | HYUNDAI 96525N7000RETQH; HYUNDAI 96525N7001NNBQH; HYUNDAI RR625N7000RETQH |
| pail | safety-systems | fluids | 2.17 | 0.394 | 36 | 200Lt Pail Tutela Tramsmission Zcs 160; 200LT PAIL TUTELA TRAMSMISSION ZCS 160; ATF WS - 5 GAL PAIL |
| מותחנים | wheels-bearings | belts-chains | 3.57 | 0.714 | 36 | מקורי-סט רצועת טיימינג + מותחנים; סוף חוט למותחנים; סט מותחנים ורצועת מנוע |
| backup | air-conditioning-heating | lighting | 10.3 | 0.798 | 35 | Backup; Backup Alarm By Ecco; Backup Camera Systm |
| controls | electrical | suspension-steering | 10.48 | 0.772 | 35 | Controls; Controls Control Panel Rea 613015; Controls Matt Black |
| libr | gearbox | filters | 1.04 | 0.147 | 35 | 17" Libra Alloy Wheel; 4run Libr Lamp Led; ABAKUS 037-025-0006 Opel Calibra A Coupe engine thermostat |
| sock | air-conditioning-heating | electrical | 4.77 | 0.692 | 35 | 1 2 Impact Socket L; 1 2 Impact Socket M; ABAKUS 004-014-0006 Fan, radiator Ø: 520mm, with electric mo |
| tailg | gearbox | body-exterior | 24.71 | 0.918 | 35 | ABAKUS 101-00-036 Tailgate strut both sides, Extention Force; ABAKUS 101-00-248 Tailgate strut Extention Force: 715N; ABAKUS 101-00-249 Tailgate strut Extention Force: 540N |
| לשרוול | gearbox | engine | 8.5 | 0.654 | 35 | בורג לשרוול רברס; בסיס לשרוול גומי ידית הילוכים; גומיה לשרוול למזרק |
| מגלש | audio-electronics | body-exterior | 1.08 | 0.333 | 35 | Leapmotor Part 000000000735774631; PORSCHE 9J0807568BA3G; SKIDPLATE COVER - RR BUMP |
| פלנג | audio-electronics | cooling | 1.88 | 0.405 | 35 | 2.2 פלנג' מים; FLANGE; RENAULT RE922000224R |
| brochure | merchandise | electrical | 1.13 | 0.359 | 34 | 09 Borrego Brochure; 13 Optima Brochure; 13 Sportage Brochure |
| לקרטר | air-conditioning-heating | engine | 1.24 | 0.397 | 34 | 06 4 FAC משחה לקרטר; HEX SI בורג לקרטר; H NOHCUO פקק לקרטר |
| עבה | fasteners | air-conditioning-heating | 7.13 | 0.806 | 34 | 45 ימין עבה AH FVP מצבר; a c condenser; A C Condenser |
| תליה | fasteners | engine | 2.33 | 0.438 | 34 | COAT HOOK; HOOK ASSY; J01 וו תליה אח' בגג |
| components | service-general | engine | 1.5 | 0.349 | 33 | Aerodynamic Body Components; Aerodynamic Body Components Side Sills; Box cover components |
| diagnostic | accessories | electrical | 1.53 | 0.561 | 33 | Airbag Diagnostic Module; Bracket Diagnostic; Cover Diagnostics Plug 129900 |
| kem | merchandise | engine | 1.59 | 0.519 | 33 | Chrysler Neon 2 Engine mount STARK SKEM-0660126; FEBEST 0510-KEMT CV joint kit Wheel Side, Front Axle; Kem Krest Hand Sanitizer 4oz 831530 |
| krest | merchandise | body-exterior | 2.1 | 0.631 | 33 | Backrest; BACKREST-1264069; BACKREST-31470520 |
| schrader | service-general | electrical | 12.3 | 0.863 | 33 | 1 25 In Schrader Vlve Cas; 2 5 In Schrader Vlve Case; 2 In Schrader Vlve Case 5 |
| דומע | merchandise | suspension-steering | 1.5 | 0.25 | 33 | DEIP דומע TNAVA שמאל קידמי; GENESIS 85860T1100VNB; hood latch support |
| להב | body-exterior | wipers-washers | 4.93 | 0.795 | 33 | 12-להב מגב; BLADE ASM; Blade Assy Wiper Rr |
| לעוגן | air-conditioning-heating | safety-systems | 1.33 | 0.2 | 33 | Cap B Front Seat; CAP'B',FRONT SEAT; NUT - LOCKING |
| מחט | suspension-steering | wheels-bearings | 8.43 | 0.686 | 33 | 1 מיסב מחטים הילוך; 2 מיסב מחטים הילוך; 3 מיסב מחטים הילוך |
| infotainm | accessories | audio-electronics | 6.96 | 0.774 | 32 | Classic Infotainment System - Black; Classic Infotainment System - Chrome; Classic Infotainment System - Universal |
| removal | service-general | engine | 3.18 | 0.584 | 32 | 20 Piece Radio Removal Kit; 3m Wheel Weight Removal Tool; 9-3 Turbo X Rear Suspension Self Levelling Removal Kit Sport |
| towing | suspension-steering | body-exterior | 11.47 | 0.828 | 32 | Aero 2 Towing Mirrors; BAR ASSY-TOWING; BAR ASY - TOWING |
| ליוניט | merchandise | air-conditioning-heating | 1.5 | 0.231 | 32 | 1X51 TNIOJ אטם ליוניט גיר; אבטחה ליוניט חום; אום ליוניט |
| מחטים | gearbox | wheels-bearings | 11.29 | 0.849 | 32 | 1 מיסב מחטים הילוך; 2 מיסב מחטים הילוך; 3 מיסב מחטים הילוך |
| actuato | suspension-steering | electrical | 9.73 | 0.734 | 31 | ABAKUS 131-06-965 Parking brake actuator / motor; A.B.S. 43684 Parking brake actuator / motor; A.B.S. 43694 Parking brake actuator / motor |
| jumasa | air-conditioning-heating | body-exterior | 2.68 | 0.603 | 31 | 21 K48 Wing mirror glass JUMASA 55024054; CITROËN AX Hatchback body JUMASA 54041040; CITROËN C15 Platform / Chassis (VDPD) Side indicator JUMASA  |
| serv | service-general | suspension-steering | 1.13 | 0.198 | 31 | 15MY LEGACY SERVC MNUAL EDITION- DVD; 1989 Maserati Bi Turbo ATE Original Used Brake Servo + Maste; 21MY LEGACY SERVC MNUAL EDITION- DVD |
| wordmar | merchandise | body-exterior | 1.64 | 0.561 | 31 | Jaguar Wordmark Cap; M Wordmark; M Wordmark T |
| סטופר | merchandise | body-exterior | 3.5 | 0.467 | 31 | F32 'סטופר דלת קד' ימ; Tail-door buffer block; גומית סטופר לגושת ברקס |
| ajusa | service-general | engine | 4.04 | 0.648 | 30 | 147 Panorama Rocker cover gasket AJUSA 11010100; 14 Hatchback Rocker cover gasket AJUSA 11022900; AJUSA 00153500 Peugeot 505 (551a) thermostat gasket |
| cams | air-conditioning-heating | engine | 4.81 | 0.779 | 30 | 3RG 86600 Camshaft seal frontal sided; ABAKUS 120-05-024 Mercedes E Class W124 camshaft sensor; ABAKUS 120-05-053 Camshaft position sensor Hall Sensor |
| לדיסקיות | audio-electronics | brakes | 4.55 | 0.602 | 30 | A G80 לדיסקיות M תפס סמל; ATTACHMENT,PAD-FRONT; Disc Brake Anti Rattle Clip Set |
| מהיר | fasteners | electrical | 4.7 | 0.578 | 30 | 12 NLS נגד מהירות למזגן דימקס מ; 12 נגד מהירות למזגן דימקס מ; 21 נגד מהירות למזגן דימקס מ |
| פלאש | merchandise | engine | 4.0 | 0.615 | 30 | חצי מנוע A5B412 ספלאש; מקורי-פלאשינג למנוע בנזין; פלאש אוויר לממיר קטליטי |
| קופסה | gearbox | electrical | 1.4 | 0.304 | 30 | Ash Tray; Automatic Transmission Shift Cover Plate; BOX,CONSOLE |
| תערובת | electrical | cooling | 5.0 | 0.714 | 30 | טאטימה לצינור תערובת אוראה; מיכל תערובת דלק; מיכסה מיכל תערובת |
| first | safety-systems | brakes | 3.84 | 0.427 | 29 | 911 Turbo First Turbo 992 1 18 Silver Re; BMW X3 E83 Brake pad set VALEO 301580; Citroen Berlingo MF Brake pad set VALEO 301474 |
| occ | body-exterior | electrical | 3.97 | 0.568 | 29 | 2 DROCCAR מעבר מחבר צנרת; 3 DROCCAR פלסטי T מעביר; 3 DROCCA זוית לנשם |
| season | wheels-bearings | interior-comfort | 2.62 | 0.604 | 29 | 245 40 R18 P6 Four Seasons M 369041; 255 40 R19 P6 Four Seasons B 369041; All Season Bw |
| shutter | body-exterior | cooling | 1.51 | 0.461 | 29 | Active grille shutter assembly - black; Actuator Shutter Valve; Actuator Shutter Vlv |
| wartung | service-general | air-conditioning-heating | 5.58 | 0.848 | 29 | 911 Garantie Und Wartungsbroschure A; Block Wartungsformulare; Cayenne Garantie Und Wartungsbroschur |
| אמש | clutch-drivetrain | lighting | 1.05 | 0.253 | 29 | actuator bracket; actuator clamp; axle assembly |
| לכוס | merchandise | interior-comfort | 2.33 | 0.571 | 29 | Boot or; BOOTS,BRAKE; HOLDER - CUP |
| לכיסוי | belts-chains | body-exterior | 3.29 | 0.534 | 29 | ADAPTER; Bracket for; Bracket ode |
| compress | engine | air-conditioning-heating | 3.72 | 0.695 | 28 | ACAUTO AC-02HA01 AC compressor control valve; ACAUTO AC-02SD04 AC compressor control valve; ACAUTO AC-02SD11 AC compressor control valve |
| epkc | accessories | electrical | 7.83 | 0.87 | 28 | EINPARTS EPKC101 YELLOW Prado 120 ignition switch; EINPARTS EPKC102 BLACK Keyless Case; EINPARTS EPKC108 LIGHT BLUE Keyless Case |
| evoque | hybrid-ev | brakes | 1.32 | 0.213 | 28 | 10&quot; Touchscreen Climate Control Upgrade Panel - Range R; 2x Magma Brake Discs Rear Solid Coated Fits Land Rover Range; 2x Vented Brake Discs and Pads fits RANGE ROVER EVOQUE L538  |
| hft | audio-electronics | engine | 1.44 | 0.345 | 28 | 10p13 15 Carbon Shft; BRNG SET-CRNKSHFT; button shft lvr knob |
| media | audio-electronics | clutch-drivetrain | 1.01 | 0.192 | 28 | 9000 94′-98′ intermediate exhaust pipe for front mount CAT; AIC 55726 Intermediate Shaft Front Axle, Right, for manual t; AJUSA 15004400 Shaft Seal, intermediate shaft |
| object | hybrid-ev | electrical | 24.0 | 0.906 | 28 | Control Unit For Object Detection; Land Rover Object; Land Rover Object Sensor |
| staple | belts-chains | fasteners | 1.33 | 0.381 | 28 | BLUE PRINT ADBP420073 Brake pad set with acoustic wear warni; BLUE PRINT ADC44272 MITSUBISHI L200 / Triton IV Pickup (KA, ; Bonnet Side Moulding Staple Clip. |
| tole | fasteners | air-conditioning-heating | 5.86 | 0.651 | 28 | Gear Tolerance; RING - TOLERANCE; TOLE ETNAJ ג'נט |
| ינדי | suspension-steering | body-exterior | 2.0 | 0.353 | 28 | Automatic Transmission Shift Indicator; BEZEL; Cover |
| לחיזוק | fasteners | body-exterior | 1.44 | 0.469 | 28 | BOLT; Impact Bar Bracket; LH פחית לחיזוק משקוף |
| לקצה | gearbox | body-exterior | 3.5 | 0.533 | 28 | BRACKET; Fender Insulator; Holder for tode |
| נטים | electrical | wheels-bearings | 4.5 | 0.692 | 28 | 0 ער' ניקוי ג'נטים+מברשת; ג'אנטים 51; ג'נטים |
| קיירון | interior-comfort | body-exterior | 2.33 | 0.318 | 28 | SsangYong Actyon - נורה בקרת אק'-קיירון; SsangYong Actyon - ראש מנוע-קיירון/אק'פ; SsangYong SsangYong - אגן שמן מנ-קיירון/אק |
| ריג | suspension-steering | wheels-bearings | 1.89 | 0.321 | 28 | 99 בריג Y; automatic transmission control solenoid; automatic transmission fluid temperature sensor |
| abakus | service-general | lighting | 1.81 | 0.338 | 27 | ABAKUS 003-07-904 Number plate light Right, with bulb; ABAKUS 004-014-0013 Fan, radiator Ø: 460mm, with electric mo; ABAKUS 004-017-0046 Engine radiator Automatic Transmission |
| iem | wheels-bearings | electrical | 3.0 | 0.444 | 27 | Bag Tags Mit Lederriemen; Cradle Siemens S55 A56 C56 Ct56 S56; Iem |
| tapp | service-general | engine | 2.63 | 0.673 | 27 | 147 Panorama Tappet FAI AutoParts BFS100; 780 Tappet FAI AutoParts BFS101S; AE FOL107 Tappet Hydraulic |
| לכימ | suspension-steering | lighting | 1.75 | 0.318 | 27 | engine coolant reservoir; engine coolant reservoir grommet; GENESIS 25430T6010 |
| סינכרונים | filters | gearbox | 1.12 | 0.474 | 27 | 901 5CJ סט סינכרונים; KIT-REPAIR; RENAULT RE7701467269 |
| צידי | audio-electronics | interior-comfort | 1.06 | 0.321 | 27 | HYUNDAI 93750KL060YFE; HYUNDAI 93750N7BD0MMH; KIT - ROCK RAIL |
| קדר | merchandise | body-exterior | 22.83 | 0.89 | 27 | DE RUEHCEL קדר גומי חלון; D ESSILUOC קדר למסגרת דלת; ENJOLIVEUR קדר חלון חזית |
| handl | hybrid-ev | body-exterior | 2.59 | 0.677 | 26 | 1 Set Grab Handles Black; A.B.S. 55373 Repair Kit, parking brake handle (brake caliper; A.B.S. 55377 Repair Kit, parking brake handle (brake caliper |
| ted | suspension-steering | brakes | 2.54 | 0.333 | 26 | 1 Set Of Sill Trim Strips Illuminated; 2BA Raised Countersunk Screw - Slotted oval head, 2"; 2x Brake Discs Pair Vented fits CHRYSLER SEBRING JS 2.0 Fron |
| weld | service-general | fasteners | 3.85 | 0.47 | 26 | 1" DIA X 125LG WELD AND PLATED PIN; 3m weld coating; 3m Welding And Spark Deflect 833520 |
| זרימת | fuel-air | electrical | 2.33 | 0.456 | 26 | 2 גרמושקה למד זרימת אויר; -43 צינור למד זרימת אויר; 6 גרמושקה למד זרימת אויר |
| להפעלת | fasteners | air-conditioning-heating | 2.55 | 0.402 | 26 | C69 פקוד להפעלת מזגן; Hvac Blower Control Switch; HYUNDAI 81680AA000 |
| מפשיר | fasteners | electrical | 5.46 | 0.676 | 26 | ETPURRETNI מתג מפשיר אדים; GLASS,WINDSHIELD; INTERRUPTEUR מתג מפשיר אדים |
| סולנויד | interior-comfort | electrical | 1.64 | 0.316 | 26 | 06-2009 סולנויד בקרת לחץ מ; OEM Part - סולנויד לגיר; אטם לסולנויד |
| צילנדרים | audio-electronics | engine | 8.5 | 0.708 | 26 | טבעת איטום כניסת מצת מנוע 8 צילנדרים; טורבו למנוע 5 צילנדרים - מחודש; מדיד שמן למנועי 6 צילנדרים |
| שקל | wheels-bearings | electrical | 7.83 | 0.712 | 26 | LR/JAG Part - בולם קדמי BIG BORE למביני עניין מתוצרת TERRAFI; LR/JAG Part - שאקל סינטטי בעובי 10 מ"מ קל משקל ויותר נוח לתפ; OEM Part - חיישן משקל מושב נוסע |
| companio | audio-electronics | air-conditioning-heating | 1.05 | 0.504 | 25 | Companion; Companion Fl; Companion Flang |
| gsp | suspension-steering | wheels-bearings | 1.09 | 0.399 | 25 | Chevrolet Matiz M200 Top strut mount bearing GSP 510069; Fiat Tipo 356 Dust cover kit, shock absorber GSP 5402790PK; GSP 201902 Drive shaft Front Axle Left, Front Axle Right, A1 |
| אטמי | wheels-bearings | engine | 10.32 | 0.749 | 25 | 000RR ער' אטמים למש' מים; 0 ער' אטמים למש' מים; 1.5 סט אטמים לראש |
| טלפון | accessories | electrical | 2.29 | 0.386 | 25 | ADAPTER,TELEPHONE; # BOX4 מודול טלפון; G11 סוללה למודול טלפון |
| קאמרי | brakes | body-exterior | 1.87 | 0.496 | 25 | Bumper Cover; Grille Sub Assy Rad; חליפי-מצבר 65Ah קאמרי רגיל - מסחרי 12 חודש |
| קדימין | suspension-steering | body-exterior | 1.47 | 0.386 | 25 | ביטנה קד בכנף קדימין; ביטנה קדלכנף קדימין; חגורת בטיחות קדימין |
| blouse | merchandise | air-conditioning-heating | 1.0 | 0.471 | 24 | Blouse; Blouse Women; Porsche Classic Blouse Olymp Luxor 3 |
| both | service-general | suspension-steering | 1.38 | 0.317 | 24 | 2X ABS Ring Sensor Ring Front Both Sides 47 Teeth For Daewoo; 3RG 86209 Windscreen washer jet both sides, for windscreen c; 555 SR-6260-M Inner tie rod both sides |
| countersunk | service-general | fasteners | 1.38 | 0.494 | 24 | 12 Countersunk; 14 Countersunk; 2BA Raised Countersunk Screw - Slotted oval head, 2" |
| differe | gearbox | clutch-drivetrain | 8.08 | 0.854 | 24 | automatic transmission differential bearing; Automatic Transmission Differential Bearing; Automatic Transmission Differential Carrier |
| eprsb | accessories | electrical | 3.17 | 0.704 | 24 | EINPARTS EPRSB01 Keyless Case; EINPARTS EPRSB02 Keyless Case; EINPARTS EPRSB03 Keyless Case |
| machine | service-general | fasteners | 2.57 | 0.543 | 24 | 18 wheel gloss black machine alloy; 20x8.5-Inch Multi-Spoke Wheel in Gloss Black with Machined A; 22x9-Inch Aluminum 6-Split-Spoke Wheel in Low Gloss Black wi |
| microph | air-conditioning-heating | audio-electronics | 1.0 | 0.307 | 24 | Bezel-Head Microph; Bluetooth Telephone System Microphone; Bracket For Microphone |
| sides | service-general | suspension-steering | 1.38 | 0.316 | 24 | 2X ABS Ring Sensor Ring Front Both Sides 47 Teeth For Daewoo; 3RG 86209 Windscreen washer jet both sides, for windscreen c; 555 SR-6260-M Inner tie rod both sides |
| אימוב | interior-comfort | electrical | 1.46 | 0.438 | 24 | ANTITHEFT FLASR; COIL,ANTENNA-IMMOVI; HYUNDAI 954012L200 |
| למשלב | safety-systems | gearbox | 1.71 | 0.429 | 24 | SUZUKI P.24415.67K20.M; אבטחה למשלב; דסקית למשלב בגיר |
| תקרה | body-exterior | lighting | 14.0 | 0.869 | 24 | BRACKET,OVERHEAD CON; Bulb; BULB |
| extractor | service-general | air-conditioning-heating | 6.73 | 0.698 | 23 | Air Extractor; Chamber Extractor; CHAMBER EXTRACTOR |
| poster | merchandise | air-conditioning-heating | 1.09 | 0.289 | 23 | 24h Le Mans 2016 Victory Poster; 60 Years Of The Porsche 911 Poster 992; 60 Years Of The Porsche 911 Poster G M |
| siv | service-general | body-exterior | 1.04 | 0.231 | 23 | 01 XEH SIV ברגים; 1 Set Double Sided Adhesive Tape; 1X7M H SIV ברגים |
| umbre | wheels-bearings | merchandise | 7.33 | 0.733 | 23 | 42" Folding Umbrella, Black; 62" Stick Umbrella, Black; Alfa Romeo Red Umbrella |
| ארוכבה | engine | wheels-bearings | 1.85 | 0.48 | 23 | אטם החזרת שמן לגל ארוכבה; אטם לגל ארוכבה בבלוק מנוע; בורג בגל ארוכבה |
| הזזה | fasteners | body-exterior | 7.71 | 0.732 | 23 | COVER; DOOR ASY - HINGED SIDE LO; DOOR-SIDE LOAD |
| לקולר | filters | cooling | 1.38 | 0.25 | 22 | Air dam, engode; Charge air i; Cooler Locating Stud 612511 |
| סנן | exhaust | filters | 21.54 | 0.843 | 22 | 01 LCI 730D מסנן חלקיקים; 1.9 מסנן אוויר; #6.7L F350 מש' דלק + מסנן |
| carpoint | service-general | body-exterior | 1.15 | 0.356 | 21 | CARPOINT 0410220 Repair panel; CARPOINT 0410230 Hitch locks; CARPOINT 0410232 Hitch locks |
| cubby | accessories | body-exterior | 2.18 | 0.421 | 21 | Audison 500W Sub Woofer Leather Cubby Box Arm Rest for Defen; Boot / Trunk Lid & Cubby Box Lock Set; Box-cubby |
| diode | electrical | air-conditioning-heating | 2.95 | 0.679 | 21 | Adapter Wiring Set With Diode; AVA COOLING SYSTEMS AIAK003 Air conditioning compressor Refr; AVA COOLING SYSTEMS AIAK271 Air conditioning compressor Refr |
| euga | brakes | body-exterior | 2.4 | 0.4 | 21 | 01X8 EUGA גומיה; 1 EUGAB תותב; 1X21 EUGAB טבעת בלוק מנוע |
| gca | suspension-steering | cooling | 1.0 | 0.378 | 21 | 21 K48 Top strut mount goCORE GCA1800484; Agile Top strut mount goCORE GCA1800492; bB I Top strut mount goCORE GCA1800144 |
| gocore | suspension-steering | filters | 1.4 | 0.243 | 21 | 147 Panorama Glow plug goCORE GCB430112; CITROËN BX Box Body / Hatchback (XB-_) Ignition coil goCORE ; CITROËN BX Box Body / Hatchback (XB-_) Water pump and timing |
| hpauto | accessories | body-exterior | 1.64 | 0.478 | 21 | HPAUTO 10100 Corrugated Pipe; HPAUTO 10200 Keyless Case; HPAUTO 11316 Jack Support Plate |
| olymp | merchandise | belts-chains | 1.18 | 0.183 | 21 | A.B.S. 200557 Olympia A Coupe wheel bearing; A.B.S. 36624/1 Olympia A Coupe brake pads; ACKOJA A38-99-0008 Olympia A Coupe engine thermostat |
| strainer | filters | fuel-air | 1.99 | 0.33 | 21 | ACKOJA A70-1054 Oil Pump Oil Sump, with strainer; Bracket Fuel Strainer 139900; Bracket Fuel Strainer 169900 |
| tier | accessories | suspension-steering | 1.33 | 0.291 | 21 | 05 19 Frontier 5 Mx4 Truck Bed Cover; 05 19 Frontier 6 Mx4 Truck Bed Cover; 05 19 Frontier Crew Avs 3m Ventvisors |
| עין | wipers-washers | cooling | 1.02 | 0.276 | 21 | Connector; Cover, spray; F-15 עין למתיז |
| drm | service-general | engine | 2.74 | 0.448 | 20 | DENSO DRM02026 Porsche Cayenne 955 engine radiator; DENSO DRM02047 VW Touareg CR engine radiator; DENSO DRM05112 Engine radiator |
| envelope | body-exterior | air-conditioning-heating | 1.88 | 0.627 | 20 | Black Mohair Hood Envelope; Black / Tan Mohair Hood Envelope; Black Vinyl Hood Envelope |
| gbs | audio-electronics | electrical | 10.5 | 0.665 | 20 | BOSCH 0 242 245 536 FORD Taunus '80 (GBS, GBNS) spark plug; BOSCH 3 397 011 353 FORD Taunus '80 (GBS, GBNS) windshield w; BOSCH 3 397 118 505 FORD Taunus '80 (GBS, GBNS) windshield w |
| hatch | body-exterior | suspension-steering | 1.08 | 0.197 | 20 | 555 SR-T900 DAEWOO Tico Hatchback tie rod; ABAKUS 024-017-0011 KIA Rio IV Box Body / Hatchback (YB, SC,; A.B.S. 200988 MG MGY Hatchback wheel bearing |
| measuring | service-general | air-conditioning-heating | 6.0 | 0.8 | 20 | Acc Measuring Unit Vw Vas 6430 2; Cam Lift Measuring D; Measuring Device |
| selec | hybrid-ev | gearbox | 6.92 | 0.622 | 20 | 3RG 23268 Selector- / Shift Rod; 3RG 23728 Gear selector cable Right, Left; ACKOJA A70-0594 Gear selector cable |
| telev | air-conditioning-heating | electrical | 1.26 | 0.479 | 20 | Antenna Assy Televi; base television; Base Television |
| עיוור | interior-comfort | body-exterior | 2.33 | 0.389 | 20 | אום עיוור כיסוי תחתון תא מנוע; אום עיוור לבורג גריל קד; אום עיוור[פחית]מבצע ACODE570/S |
| restagraf | service-general | body-exterior | 4.94 | 0.717 | 19 | Metalcaucho 40865 Door Catch Left Rear, Right Rear; RESTAGRAF 10262 Trim clip; RESTAGRAF 10330 Trim clip |
| scu | air-conditioning-heating | body-exterior | 2.07 | 0.37 | 19 | 3RG 85261 FIAT E-Scudo Minibus engine thermostat; ABAKUS 017-025-0014 Fiat Scudo 270 engine thermostat; ABAKUS 038-025-0012 Fiat Scudo 270 engine thermostat |
| sebring | suspension-steering | brakes | 1.64 | 0.289 | 19 | 1x ABS Sensor Front Left for CHRYSLER SEBRING 07-; 1x ABS Sensor Rear Right for CHRYSLER SEBRING 07-; 1x Brake Caliper Front Left for CHRYSLER SEBRING 07-10, DODG |
| sma | suspension-steering | air-conditioning-heating | 4.4 | 0.517 | 19 | A.B.S. 201882 Renault Talisman Grandtour wheel bearing; A.B.S. 210378 Mitsubishi Carisma DAO control arm; AGR small paode |
| כדור | gearbox | wheels-bearings | 1.48 | 0.452 | 19 | 1 IB1 TL מיסב כדורי; 3 IB1 TLR מיסב כדורי; 4.95 כדורית מרווח |
| bellow | service-general | suspension-steering | 4.02 | 0.516 | 18 | 10 10 1948 SWAG Bellow Set, steering; 33 10 1432 SWAG Bellow Set, drive shaft; 33 10 2215 SWAG Bellow Set, drive shaft |
| embossed | hybrid-ev | interior-comfort | 2.89 | 0.591 | 18 | Armrest Black Embossed; Centre Armrest Embossed Leather Black; Envision 2 Ply Embossed Toilet Tissue |
| isa | service-general | body-exterior | 1.14 | 0.237 | 18 | 3RG 40260 CITROËN Visa Convertible engine mount; Alfa e-Parts AF10436 CITROËN Visa Convertible engine thermos; AS-PL S0309 CITROËN Visa Convertible engine starter |
| ncm | clutch-drivetrain | lighting | 2.0 | 0.5 | 18 | 92435AR000NCM; 92436AR000NCM; GENESIS 82651T6500NCM |
| network | electrical | air-conditioning-heating | 2.54 | 0.709 | 18 | computer network ga; Computer Network Ga; Control Unit, Network Cu |
| regulat | electrical | body-exterior | 2.06 | 0.464 | 18 | ABAKUS 135-02-030 Headlight switch without integrated regula; ACKOJA A52-79-0014 Blower motor regulator; AC Rolcar 01.4450 PEUGEOT 307 SW Box Body / Estate (3E_, 3H_ |
| גזים | air-conditioning-heating | electrical | 1.34 | 0.304 | 18 | 06 LCI חיישן לבדיקת גזים; 56 LCI חיישן לבדיקת גזים; Aluminum casode |
| לחות | gearbox | brakes | 4.93 | 0.664 | 18 | (17") 'צלחות בלם קד; BOLT; HUB AND DISC ASY |
| מכוונים | wipers-washers | electrical | 1.75 | 0.452 | 18 | BULB; Courtesy Light Bulb; GENESIS 96560CU220 |
| מסג | engine | body-exterior | 2.19 | 0.474 | 18 | 92-95 מסג.לגריל לג'נט; AERO UNDER SHIELD ASM - FRT SUBFRAME; BRACKET,AUDIO |
| תידי | merchandise | lighting | 1.33 | 0.417 | 18 | cover fr door outsi; DE תידי TREVUO פתיחה מכסה; door panel handle cover |
| cardboard | merchandise | air-conditioning-heating | 1.47 | 0.595 | 17 | Cardboard; Cardboard Box 813010 |
| dat | clutch-drivetrain | fuel-air | 1.58 | 0.256 | 17 | 900 88′-93′ 12 Groove Black Diamond REAR Discs & Predator Pa; ACKOJA A52-81-0002 Throttle body Ø: 57mm, with gasket/seal, ; ACKOJA A53-81-0005 Throttle body with gasket/seal, Control U |
| fto | gearbox | suspension-steering | 1.4 | 0.248 | 17 | 555 SB-7761 FTO suspension ball joint; ABAKUS 033-025-0002 FTO engine thermostat; ACKOJA A37-0071 FTO control arm |
| ilh | air-conditioning-heating | body-exterior | 1.88 | 0.441 | 17 | FEBEST 2411-KANGIILH Logan II MCV cv joint; FEBEST 2411-KANGIILH Mercedes Citan Panel Van cv joint; FEBEST 2411-KANGIILH NISSAN Bluebird Sylphy II Saloon (G11)  |
| pouch | accessories | air-conditioning-heating | 2.75 | 0.537 | 17 | Bahama Pouch Tropical Breeze; Cargo Net Without Spare Collapsible With Storage Pouch; Cargo Net W Pouch Models W 3rd Row |
| vitre | electrical | body-exterior | 1.3 | 0.394 | 17 | DE VITRE השמש C דופן אחורי; VITRE; VITRE DVA. שמשה דלת קד ימ |
| גרי | merchandise | body-exterior | 24.86 | 0.868 | 17 | # '17 F350 'גריל במגן קד; 2010 CRV ניקל עליון שמאלי לגריל; 92-95 מסג.לגריל לג'נט |
| קדשמאל | suspension-steering | body-exterior | 2.0 | 0.375 | 17 | ביטנה קד לכנף קדשמאל; חגורת בטיחות קדשמאל; כנף קדשמאל |
| dril | service-general | brakes | 1.6 | 0.429 | 16 | 1 2 Rev Drill; 262mm FRONT DRILLED GROOVED BRAKE DISCS FOR ROVER MG ZR ZS 2; 31 Piece Zebra Drill |
| exterior | body-exterior | interior-comfort | 1.18 | 0.468 | 16 | ABAKUS 017-71-861 Side indicator Left Exterior Mirror, witho; ABAKUS 017-71-861S Side indicator Left Exterior Mirror, with; ABAKUS 018-11-862 Side indicator Right Front, Exterior Mirro |
| nubira | suspension-steering | brakes | 1.49 | 0.301 | 16 | 06800 BRAKE SHOE SET KIT REAR LPR FOR DAEWOO NUBIRA,NEXIA,ES; 18022716S3 abs 112008 DAEWOO NUBIRA 1.6 G 1997-1999 1998; 1810098 BRAKE LIGHT SWITCH STOP EPS FOR DAEWOO LANOS,NUBIRA, |
| סולוניד | merchandise | electrical | 4.0 | 0.533 | 16 | מתג סולוניד תה'ל,ר 12; סולוניד; סולוניד ואקום |
| asa | service-general | body-exterior | 2.93 | 0.521 | 15 | Armrest Basalt Black; Armrest Basalt Black Black; Asa Bolt 889522 |
| esiotertn | hybrid-ev | fasteners | 1.33 | 0.308 | 15 | ESIOTERTN דיסקית; ESIOTERTN דיסקית מרווח; ESIOTERTN תותב |
| force | service-general | body-exterior | 27.69 | 0.909 | 15 | ABAKUS 053-36-360 Bumper reinforcement Front; ABAKUS 101-00-036 Tailgate strut both sides, Extention Force; ABAKUS 101-00-131 Bonnet strut Extention Force: 310N, both s |
| integ | service-general | wheels-bearings | 1.03 | 0.187 | 15 | 11 Rear Integr; 12 Rear Integr; 14 Rear Integr |
| loaner | service-general | air-conditioning-heating | 14.0 | 0.933 | 15 | 6913d Loaner Car Expense; 7514d Loaner Car Expense; 7714f Loaner Car Expense |
| netwo | electrical | air-conditioning-heating | 1.8 | 0.638 | 15 | computer assy netwo; Computer Assy Netwo; computer network ga |
| ods | gearbox | suspension-steering | 1.39 | 0.363 | 15 | 1/2 Wing - No Pods; 1 Set Connecting Rods; 6x Windscreen Wiper Linkage Rods Repair Clip Spring Fits Alm |
| pferd | service-general | suspension-steering | 1.42 | 0.48 | 15 | PFERD 22000003 Bearing; PFERD 22000004 Bearing; PFERD 22000014 Repair Kit |
| tappi | service-general | fasteners | 13.6 | 0.883 | 15 | Cap Tapping Screw; Fillister Head Self Tapping 889522; Fillister Head Self Tapping C000090 |
| unt | service-general | engine | 1.45 | 0.342 | 15 | 2BA Raised Countersunk Screw - Slotted oval head, 2"; 3RG 81653_3 NISSAN NV300 Van (X82) engine mount; 4MAX 0219-14-0066P Exhaust rubber mount |
| voyager | suspension-steering | brakes | 1.75 | 0.304 | 15 | 101-1015 DRUM WHEEL BRAKE CYLINDER PAIR REAR CIFAM 2PCS FOR ; 16018401 left front brake caliper 114052 CHRYSLER GRAND VOYA; 1x Brake Caliper Front Right for CHRYSLER GRAND VOYAGER RG/R |
| יחי | gearbox | electrical | 2.47 | 0.457 | 15 | Abs Control Unit; AWM REAR; BODY CONTROL MODULE |
| למכל | fuel-air | body-exterior | 1.0 | 0.195 | 15 | HOSE-OVERFLOW; OEM סט ברגים למכלול ידיות תאורה; Zeekr Wiper Blade 8889917469 |
| קבועה | interior-comfort | body-exterior | 8.83 | 0.855 | 15 | GENESIS 82580T1011; GENESIS 87820T4000; GLASS-DOOR VENT |
| automa | service-general | gearbox | 9.05 | 0.69 | 14 | 9-5 2002 to 2010 all automatic transmission models – Drivesh; 9-5 99′-10′ RHD Automatic Brake Servo Vacuum Hose – Alternat; ABAKUS 004-017-0046 Engine radiator Automatic Transmission |
| binding | service-general | fasteners | 43.0 | 0.925 | 14 | Binding Head Screw T99v00s; Camping Tent Binding; Carpet Binding |
| casquillo | fuel-air | air-conditioning-heating | 13.0 | 0.929 | 14 | CASQUILLO; CASQUILLO 14X22X57; Casquillo Basculante |
| esen | service-general | electrical | 1.14 | 0.152 | 14 | ESEN SKV 00SKV521 Renault Scenic 2 window regulator; ESEN SKV 00SKV801 Kia Magentis GD window regulator; ESEN SKV 01SKV472 Megane 2 CC window regulator |
| hexalobular | service-general | fasteners | 12.42 | 0.892 | 14 | bolt hexalobular; Bolt Hexalobular; Hexalobular Flange Screw |
| markt | service-general | merchandise | 7.8 | 0.886 | 14 | Garantie Und Wartungsbroschure A Markt; Maintenance Booklet 911 R A Markt; Maintenance Booklet 918 Spyder C Markt |
| reducer | service-general | clutch-drivetrain | 13.4 | 0.713 | 14 | Air Flow Reducer Hme46s; Air Reducer C113050; Air Reducer C116097 |
| shifting | gearbox | body-exterior | 1.19 | 0.408 | 14 | Bearing Shifting Arm 251030; Bearing Shifting Arm C251030; bezel shifting hole |
| skv | service-general | electrical | 1.14 | 0.149 | 14 | ESEN SKV 00SKV521 Renault Scenic 2 window regulator; ESEN SKV 00SKV801 Kia Magentis GD window regulator; ESEN SKV 01SKV472 Megane 2 CC window regulator |
| swi | accessories | electrical | 8.94 | 0.636 | 14 | 3RG 80469 Hazard warning light switch; ABAKUS 135-04-006 Steering column switch; ABAKUS 135-06-114 Window switch Right Front |
| volume | audio-electronics | electrical | 1.95 | 0.438 | 14 | Air Volume Determinator With C136032; ARB On Board Twin Air Compressor - High Volume Air Flow; case switch volume |
| למנגנון | interior-comfort | body-exterior | 1.27 | 0.371 | 14 | 57 כיסוי אח' למנגנון גג; ABS מחשב למנגנון; ABS תושבת למנגנון |
| מיחזור | exhaust | engine | 1.38 | 0.353 | 14 | SsangYong Korando - מקרן מיחזור שסתום EGR-קורנדו II; SsangYong Korando - צנור יציאה מיחזור EGR-קורנדו II; SsangYong Korando - שסתום מיחזור EGR-קורנדו II |
| מעמד | fasteners | body-exterior | 2.5 | 0.385 | 14 | 00RR בורג לתושבת מעמד צד; '13 F800GS מעמד מרכזי; 650GT בורג תושבת מעמד צד |
| city | suspension-steering | fluids | 1.29 | 0.398 | 13 | A.B.S. 35020 TOYOTA Proace City Van brake pads; A.B.S. 7522 Brake Fluid Capacity: 0,25l; ALTE AUTOMOTIVE 99699AL TOYOTA Proace City Verso suspension  |
| crossrecess | service-general | fasteners | inf | 1.0 | 13 | Screw Crossrecess; screw crossrecess b; Screw Crossrecess B |
| nexia | suspension-steering | brakes | 2.1 | 0.303 | 13 | 06800 BRAKE SHOE SET KIT REAR LPR FOR DAEWOO NUBIRA,NEXIA,ES; 0 986 475 032 DRUM WHEEL BRAKE CYLINDER REAR BOSCH FOR DAEWO; 1997 DAEWOO NEXIA ABS Pump/Modulator |
| posit | electrical | engine | 1.21 | 0.387 | 13 | ABAKUS 120-05-053 Camshaft position sensor Hall Sensor; ABAKUS 120-05-078 Camshaft position sensor; ABAKUS 120-08-003 Throttle position sensor |
| punch | service-general | brakes | 1.67 | 0.357 | 13 | Brake Disc Ventil W Punched 349045; BUMPER PUNCHING RXV; Frt Slow Down Punch Block |
| vnb | clutch-drivetrain | lighting | 1.2 | 0.3 | 13 | 85220T6710VNB; GENESIS 56100T1700VNB; GENESIS 82307AR340VNB |
| winch | accessories | electrical | 3.65 | 0.564 | 13 | 2000lb Elec Winch; 4 X30 Winch Strap; Anti-Theft Spare Wheel Winch for Spare Tyre Space Saver on R |
| הלוכים | gearbox | interior-comfort | 3.25 | 0.456 | 13 | BRACKET,AUDIO; DE LEVIER ידית הלוכים; EDC ELBA כבל ידית הלוכים |
| לירגל | merchandise | body-exterior | 1.75 | 0.583 | 13 | grille molding; grille surround; LEXUS 5339548060 |
| למאורר | lighting | engine | 2.0 | 0.448 | 13 | 3.0 מצמד למאורר דיזל; Blower Motor; DE ECILEH מניפה למאורר |
| למזין | clutch-drivetrain | fasteners | 2.5 | 0.333 | 13 | אום למזין מגרסה; בורג למזין מגרסה; בית מייסב למזין מגרסה |
| accessories | service-general | brakes | 3.14 | 0.442 | 12 | A.B.S. 111411 Drum brake kit Rear Axle, with wheel brake cyl; A.B.S. 111441 Drum brake kit Rear Axle, with wheel brake cyl; A.B.S. 111455 Drum brake kit Rear Axle, with wheel brake cyl |
| autostyle | body-exterior | suspension-steering | 1.58 | 0.45 | 12 | AutoStyle AC IH440 Coil spring buffer; AutoStyle AC KT05 Wing mirror housing; AutoStyle AC KT13C Wing mirror housing |
| cartridge | filters | air-conditioning-heating | 1.38 | 0.306 | 12 | 1 2 Cartridge R; 1 4 Cartridge R; 3 8 Cartridge R |
| float | fuel-air | engine | 2.86 | 0.497 | 12 | 2 Floating Brake Caliper Rear Left Right for Chrysler Grand ; 2x Floating Brake Caliper Rear For Land Rover Discovery III ; 2x Floating Brake Caliper Rear Left Right for Chrysler Grand |
| hpa | air-conditioning-heating | electrical | 8.39 | 0.812 | 12 | 410 Ss Phpan H; AM EHPARGA ביטחונית לידית; Bmper Ppfwithpa Lx |
| overd | brakes | engine | 1.54 | 0.383 | 12 | Bolt Overdrive Case; CARRIER - OVERDRIVE PLANE; Carrier Overdrive Planet Gea |
| pil | filters | body-exterior | 1.58 | 0.53 | 12 | A Pillar Primed; Apillar upper right shield assembly bla; Body A Pillar |
| rms | service-general | engine | 1.29 | 0.225 | 12 | Coat Of Arms Button Gold Black Red; Exhaust Rocker Arms; FEBEST HAB-RMS Trailing arm / Suspension arm bush Lower Fron |
| ssm | suspension-steering | body-exterior | 1.57 | 0.404 | 12 | Bar Rr Susp Crossmember; Block Fr Crossmembe; Bolt, Front Axle Crossmembe |
| temperatur | electrical | cooling | 2.83 | 0.632 | 12 | A C Evaporator Temperature Sensor; AIC 51908 Sensor, coolant temperature; AIC 75177 Thermostat with gasket/seal, Opening Temperature:  |
| tent | accessories | body-exterior | 9.6 | 0.837 | 12 | ABAKUS 101-00-024 Tailgate strut both sides, Extention Force; ABAKUS 101-00-036 Tailgate strut both sides, Extention Force; ABAKUS 101-00-058 Tailgate strut both sides, Extention Force |
| transport | gearbox | suspension-steering | 1.48 | 0.189 | 12 | 3RG 24792 T4 Transporter repair kit gear lever; 3RG 80700 T4 Transporter fuel pump; 555 SA-6191R VW T3 Transporter control arm |
| windage | interior-comfort | engine | 1.44 | 0.481 | 12 | engine oil sump windage tray; Engine Oil Sump Windage Tray; Engine Oil Sump Windage Tray Gasket |
| טייפ | merchandise | cooling | 1.93 | 0.58 | 12 | RADIOTAPE JVC; טייפ; טייפר לוק "2/1+2 X 7152M |
| לשמשות | air-conditioning-heating | electrical | 1.0 | 0.458 | 12 | ACLETTE מגב לשמשות אח; KIT-JET; SD יח' מתגים לשמשות |
| ניהול | electrical | engine | 4.76 | 0.783 | 12 | # 11 EDG מחשב ניהול מנוע; # '25 מחשב ניהול מנוע אקס; Airbag ECU |
| סאב | fuel-air | audio-electronics | 2.33 | 0.368 | 12 | BOLT; Central bassode; Central bassor |
| קראנק | wheels-bearings | electrical | 1.25 | 0.27 | 12 | Leapmotor Part 000000001611187480; Leapmotor Part 000000001654517380; Leapmotor Part 000000009688606380 |
| beari | service-general | wheels-bearings | 5.39 | 0.668 | 11 | 780 Top strut mount bearing OPTIMAL F8-6267; ABAKUS 141-01-019 Wheel bearing kit Front Axle; ABAKUS 141-03-055 Wheel bearing kit Rear Axle Left, Rear Axl |
| buy | service-general | air-conditioning-heating | 2.5 | 0.439 | 11 | Balancer Buyback 2018; Balancer Buyback 2019; Balancer Buyback 2020 |
| exhaus | audio-electronics | exhaust | 19.54 | 0.867 | 11 | 3RG 70609 Exhaust hanger; 3RG 71603 Exhaust pipe gasket; 4MAX 0219-01-04806P Exhaust Pipe Front |
| idle | engine | belts-chains | 2.64 | 0.457 | 11 | 900 (94-98), 9000 (94-98) & 9-5 Petrol V6 (98-10) Timing Idl; 9-3 98′-02′ 4CYL Petrol all models : Idler Pulley (B) – Genu; 9-3 Sports 04′-09′ 1.8 (Z18XE) Petrol Engines: Timing Belt I |
| overflow | cooling | fuel-air | 1.69 | 0.603 | 11 | ACKOJA A38-0636 Fuel overflow hose suitable for biodiesel; ACKOJA A52-0527 Fuel overflow hose suitable for biodiesel; AIC 74807 Fuel overflow hose suitable for biodiesel |
| sportwag | service-general | air-conditioning-heating | 1.36 | 0.528 | 11 | Betriebsanleitung Cdr 30 Sportwagen; Betriebsanleitung Pcm3 0 Sportwagen; Betriebsanleitung Pcm Ii Sportwagen |
| witho | accessories | lighting | 1.22 | 0.177 | 11 | 1 Set Of Lock Cylinders Without Specific; ABAKUS 017-026-003 Coolant expansion tank without lid; ABAKUS 037-34-454 Opel Astra H bumper grill |
| אזעקה | safety-systems | electrical | 6.14 | 0.694 | 11 | 2.0 מנעול דיסק+ אזעקה; 3 מנעול דיסק+ אזעקה אדום; 3 מנעול דיסק+ אזעקה ירוק |
| וזון | merchandise | wheels-bearings | 2.25 | 0.308 | 11 | 1X01NOJUOG גוזון גיר; 1X8 NOJUOG גוזון מניפולד; 4/3NOLEMA גוזון מסנן שמן |
| משופר | fluids | brakes | 1.57 | 0.248 | 11 | 0GS 'מיסב משופר לגלגל אח; 2012-2024 נעלי בלם אח' (סט משופר ללא קפיצים) גבוה; 4 משמרת באגן שמן משופרת 4 ר |
| autofren | suspension-steering | brakes | 2.96 | 0.558 | 10 | AUTOFREN SEINSA D025120 Brake caliper piston Rear Axle; AUTOFREN SEINSA D025120K Brake caliper piston Rear Axle; AUTOFREN SEINSA D025133 Brake caliper piston Front Axle |
| brk | brakes | electrical | 1.6 | 0.267 | 10 | ADJSTR-REAR BRK LH; Air Brke Nyl 1; Antenna Brkt B Keyless |
| conductor | electrical | air-conditioning-heating | 1.22 | 0.317 | 10 | 1 Set Of Conductor Rails With Fuse; Aingle Conductor Seal 889520; conductor kit tract |
| dwr | audio-electronics | cooling | 1.11 | 0.27 | 10 | Ag Chrome Lf Hrdwr; Ay Cuttingcord Wndwr; Cam Hrdwre |
| grit | service-general | exhaust | 5.4 | 0.75 | 10 | Bmw Bucket W Grit Guard 831510; Clear Anti Slip Grit T; Disc Bristle 3 Inch50 Grit G 759012 |
| hawk | merchandise | body-exterior | 12.18 | 0.832 | 10 | Antenna Assembly Radio And Xm B92p Nighthawk Black Pearl; Antenna Assembly Telematics B92p Nighthawk Black Pearl; antenna assembly xm b92p nighthawk black pearl |
| lane | safety-systems | engine | 1.18 | 0.199 | 10 | Camera Assy Lane Keep; Carrier Assy A T Planet; Carrier Assy Planet |
| loadsp | engine | wheels-bearings | 1.1 | 0.256 | 10 | Almond Loadspace Cover Trim for Range Rover Sport 2005-2013; Boot Loadspace Cover Trim in Ebony Black for Range Rover Spo; Discovery 5 Rear Loadspace LED Light |
| nate | fasteners | body-exterior | 1.06 | 0.324 | 10 | 1 Set Of Sill Trim Strips Illuminated; Aceite Magnatec; BSG BSG 30-116-179 Inlet manifold gasket HNBR (hydrogenated  |
| payen | service-general | engine | 6.72 | 0.779 | 10 | Carry Truck DA Rocker cover gasket PAYEN JN886; CITROËN C3 Aircross II (CC24) engine PAYEN HM5408; CITROËN CX II Hatchback Rocker cover gasket PAYEN JN292 |
| process | service-general | electrical | 2.94 | 0.465 | 10 | Body Process Module; Body Processor Module; Body Processor Module - Left Hand |
| seinsa | suspension-steering | brakes | 2.96 | 0.558 | 10 | AUTOFREN SEINSA D025120 Brake caliper piston Rear Axle; AUTOFREN SEINSA D025120K Brake caliper piston Rear Axle; AUTOFREN SEINSA D025133 Brake caliper piston Front Axle |
| syn | fluids | gearbox | 1.51 | 0.373 | 10 | 5 Litres of Saab Premium Longlife 0W-40 Fully Synthetic Oil; 75w 90 Semi Synthetic Gl5 Diff Oil; Axel Transmission fluid SPECOL 101006 |
| thule | accessories | body-exterior | 3.83 | 0.561 | 10 | CROSSBAR SET THULE; mopar roof rack removable thule; rfrck thule caprock |
| tract | electrical | air-conditioning-heating | 1.85 | 0.33 | 10 | 1ST ROW RETRACTOR ASSEMBLY LH; belt and retractor; Belt And Retractor |
| ura | clutch-drivetrain | body-exterior | 1.29 | 0.157 | 10 | 245 55 R17 102h Cinturato P7 369041; 275 40r18 99y Cinturato P7 R 369041; 2x BRAKE DISC 09.B266.11 FOR INFINITI M/35/45 M35 M45 NISSAN |
| zur | service-general | air-conditioning-heating | 1.07 | 0.291 | 10 | 911 Carrera Gts Cabriolet 1 18 Azure Blu; 911carrera Turbo Beilage Zur Betriebsanl; Azure Blue |
| בוכנת | filters | engine | 6.06 | 0.632 | 10 | Back door left pneumatic strut lower sup; Back door left pneumatic strut support a; Back door right pneumatic strut lower su |
| גוברנר | merchandise | electrical | 1.75 | 0.333 | 10 | EDC ELBAC כבל גוברנר; ELBAC לבכ VUOG גוברנר לאוט; ELBA כבל גוברנר |
| גלם | accessories | body-exterior | 1.1 | 0.379 | 10 | # 18 F-150 מפתח גלם; #1 SEL+(מפתח גלם אדג'(קטן; #1 מפתח גלם אקס 07'+טאורוס |
| הרמה | accessories | engine | 1.57 | 0.27 | 10 | ARM ASY - VALVE ROCKER; CYLINDER ASY - MASTER; END - SPINDLE ROD CONNECTING |
| טורים | interior-comfort | merchandise | 1.04 | 0.385 | 10 | 13428C; DE ELUSPAC מגביר טורים; ELBMESNE מגביר טורים |
| מוס | interior-comfort | body-exterior | 1.11 | 0.266 | 10 | 1.9 תרמוסטט; # 2.3L נשם מנוע מוסטאנג; 40 מוסטאנג A FVP מצבר |
| מיחזו | filters | engine | 1.38 | 0.353 | 10 | SsangYong Korando - מקרן מיחזור שסתום EGR-קורנדו II; SsangYong Korando - צנור יציאה מיחזור EGR-קורנדו II; SsangYong Korando - שסתום מיחזור EGR-קורנדו II |
| מכנס | merchandise | engine | 5.82 | 0.604 | 10 | 2XL RAINLOCK מכנס גשם; 36 מכנס לרוכב אישה; 36 מכנס נשים |
| עמעם | exhaust | interior-comfort | 1.0 | 0.231 | 10 | דודו עמעם; דוד עמעם; דוד עמעם אחורי |
| קפיצים | suspension-steering | brakes | 3.25 | 0.582 | 10 | 2012-2024 נעלי בלם אח' (סט משופר ללא קפיצים) גבוה; ARDWARE עמ K קפיצים לבלם; ATTACHMENT,PAD-FRONT |
| תמסו | fasteners | gearbox | 10.44 | 0.77 | 10 | Leapmotor Part 000000000032840345; ORA IL15EAB2; אגן שמן בית תמסורת א |
| basalt | air-conditioning-heating | interior-comfort | 1.48 | 0.463 | 9 | A Pillar Trim Basalt Black; Armrest Basalt Black; Armrest Basalt Black Black |
| cassette | audio-electronics | air-conditioning-heating | 1.58 | 0.452 | 9 | 14FT GRID CASSETTE; 9000 85′-98′ Direct Ignition Cassette – Genuine; 9-3 98′-00′ Direct Ignition Cassette – Aftermarket |
| dist | electrical | engine | 3.2 | 0.511 | 9 | 35DM8 Distributor; 3RG 10915 Gear, distributor shaft; ACKOJA A38-70-0038 Distributor Cap Number of connectors: 4 |
| ema | suspension-steering | electrical | 1.35 | 0.203 | 9 | A.B.S. 30955 Mazda Premacy cp abs sensor; ABS Sensor fits ROVER 45 RT 2.0 Front Left 00 to 05 20K4F Wh; Antenna Assembly Radio And Telematics And Gps And Xm R548p B |
| emergency | safety-systems | interior-comfort | 1.05 | 0.259 | 9 | 1 Set Lock Cylinders With Emergency Key; 3 Min Emergency Eyew; 500 Peak Amp Comp Emergency Jump Starter |
| gener | electrical | wheels-bearings | 1.97 | 0.412 | 9 | 2019-2023 Dodge Ram 2500 - 3500 Diesel 5th Generation Front ; Belt For Generator; bolt motor generato |
| interval | service-general | exhaust | 1.02 | 0.454 | 9 | AIC 51424 Switch, wipe interval control; CASCO CEF74300AS Fan, radiator D1: 405mm, for vehicles with ; CASCO CEF76300AS Fan, radiator D1: 312mm, for vehicles with/ |
| lokut | service-general | fasteners | 11.5 | 0.92 | 9 | Lokut Nut; Nut-lokut; Nut Lokut |
| mechatr | service-general | suspension-steering | 3.0 | 0.569 | 9 | Assy-mechatr; Assy-Mechatrnc Valve; ASTA S-XDS7 Mechatronics, automatic transmission |
| organizer | accessories | air-conditioning-heating | 1.4 | 0.527 | 9 | arb cargo organizer; Arb Cargo Organizer; Auto Document Organizer |
| polo | accessories | merchandise | 3.62 | 0.463 | 9 | 50/50 Team Polo; 50/50 Team Polo, Nvy, S; 911 Boot Lid Polo Red |
| reinz | service-general | engine | 5.93 | 0.826 | 9 | 100 Convertible Rocker cover gasket REINZ 71-35418-00; 131 Panorama Rocker cover gasket REINZ 71-31733-00; 14 Hatchback Rocker cover gasket REINZ 71-23307-00 |
| rgr | clutch-drivetrain | body-exterior | 6.5 | 0.612 | 9 | 235 55r20 Gy Ultrgrp Perf; 285 45r20 Gy Ultrgrp Perf; B Cargrapic |
| roo | interior-comfort | body-exterior | 2.97 | 0.486 | 9 | air deflector sunroof; Antenna Assembly Roof B588p Fathom Blue Pearl; Antenna Assembly Roof Nh830m Lunar Silver Metallic |
| sod | service-general | electrical | 1.03 | 0.206 | 9 | A/C compressode; Additional sode; Air spring sode |
| sor | interior-comfort | electrical | 1.55 | 0.363 | 9 | ABAKUS 120-01-030 Parking sensor black, grey, Ultrasonic Sen; ABAKUS 120-01-044 Parking sensor black, Ultrasonic Sensor, F; ABAKUS 120-02-069 Mercedes CL203 abs sensor |
| supplem | accessories | air-conditioning-heating | 2.46 | 0.349 | 9 | 2023 Macan Supplement; A506 Cayenne Om Supplement Childseat; Adapter Airbag Supplementary Tester Vas |
| theft | safety-systems | electrical | 2.6 | 0.566 | 9 | 1 Set Wheel Studs Anti Theft; 1 Set Wheel Studs Anti Theft Wheel Bolt; ABAKUS 132-017-002 Bonnet Lock without anti-theft device |
| tpo | gearbox | interior-comfort | 3.0 | 0.5 | 9 | Hammock Stand Outpos; SAIC MAXUS G50 NEW AUTO PARTS CAR SPARE AUTO CAMSHAFTPOSITIO; Setpoint Tire Gauge |
| wintercontact | wheels-bearings | air-conditioning-heating | 2.01 | 0.629 | 9 | 225 40 R18 92v Wintercontact 369042; 225 45 R18 95v Wintercontact 369042; 245 45r19 102v Wintercontact 369042 |
| wordmark | merchandise | body-exterior | 1.67 | 0.571 | 9 | Jaguar Wordmark Cap; M Wordmark; M Wordmark T |
| סופית | electrical | exhaust | 1.14 | 0.415 | 9 | 100) בידוד אדום+SZ סופית; 200XMATE-N-LOCK F2 סופית; 200 בחבילה M150 סופית |
| boost | engine | brakes | 2.19 | 0.517 | 8 | 9-3SS 06′-10′ all 2.8 V6 models (B284) -Brake Booster Pipe R; AIC 56486 Sensor, boost pressure with gasket/seal; Alfa e-Parts AF04616 Prado 120 map sensor |
| conveyor | service-general | interior-comfort | 13.0 | 0.857 | 8 | CONVEYOR; CONVEYOR zekex miilbx; FEEDER CONVEYOR TAIL DRUM SHAFT |
| dir | gearbox | engine | 1.59 | 0.328 | 8 | 2161 EGDIRTRAC NIP; 3 2 Directional Control Valv C131099; 3m Dirt Trap Material Wall A 833520 |
| dome | lighting | electrical | 1.7 | 0.533 | 8 | AIC 54103 Sensor, odometer; Bracket Dome Lamp; Bracket Strut Dome Brace R 414070 |
| failure | service-general | electrical | 1.36 | 0.5 | 8 | Bulb Failure Module; Bulb Failure Module Mounting Bracket; Bulb Failure Module Mounting Bracket RH |
| form | service-general | suspension-steering | 2.02 | 0.225 | 8 | 9-5 1998-2001 do88 Performance Alloy Radiator – Petrol Engin; A.B.S. 17732 MERCEDES-BENZ Sprinter 3-T Platform/Chassis (W9; A.B.S. 37379 MERCEDES-BENZ Sprinter 5-T Platform/Chassis (W9 |
| gates | suspension-steering | belts-chains | 2.79 | 0.581 | 8 | 14 Hatchback V-Belt GATES 6216MC; 18 Van V-Belt GATES 6275MC; 21 K48 Timing Belt GATES 5056 |
| hms | hybrid-ev | lighting | 1.53 | 0.329 | 8 | Ballast Resistor, 1.6ohms; Cam Follower Hms141; Camshaft Exhaust Hms141 |
| lashing | accessories | body-exterior | 2.92 | 0.648 | 8 | AMiO 01276 Flashing beacon; AMiO 01277 Flashing beacon; AMiO 02294 Flashing beacon |
| mechatroni | service-general | suspension-steering | 4.5 | 0.712 | 8 | ASTA S-XDS7 Mechatronics, automatic transmission; BOGAP A2130100 Repair Kit, mechatronics (automatic transmiss; BOGAP A2130102 Repair Kit, mechatronics (automatic transmiss |
| mor | gearbox | body-exterior | 1.37 | 0.48 | 8 | 1 Set Trims Sycamore Wood; 9-3 03′-09′ 4D/5D Complete Mirror LH (Heated, Manual Fold, w; ABAKUS 3905M08 Wing mirror for electric mirror adjustment, H |
| nyloc | service-general | fasteners | 2.1 | 0.604 | 8 | 109 Nylock Nut; 18 8 Ss Nylock; 88 Nylock Nut |
| optic | electrical | air-conditioning-heating | 3.58 | 0.523 | 8 | ABAKUS 0401G12 Wing mirror glass Right, with optical warning; Adapter Fiber Optic Conducto 639900; Adapter Plug Fiber Optic Co 519902 |
| origin | suspension-steering | engine | 1.01 | 0.165 | 8 | 2x Original A.B.S. Brake Disc 16989 for Infiniti Nissan; BOGAP P7117120 CITROËN C3 Origin III abs sensor; BOSCH 3 397 011 402 Wiper blade Standard, Length: 650mm, Len |
| palier | fasteners | suspension-steering | 3.33 | 0.476 | 8 | AR PALIER תושבת מתלה אח; AR PALIER תושבת ציר אח' י; AR PALIER תושבת ציר אח' ש |
| pedestrian | safety-systems | electrical | 4.0 | 0.581 | 8 | Adapter Wire For Pedestrian 611016; chamber pedestrian; Crash Sensor For Pedestrian Protection |
| skodp | service-general | engine | 2.75 | 0.688 | 8 | HONDA City VII Hatchback engine cooling system STARK SKODP-2; STARK SKODP-2570003 Sump plug washer Copper; STARK SKODP-2570005 Sump plug washer Aluminium |
| variant | service-general | suspension-steering | 1.64 | 0.21 | 8 | ABAKUS 053-025-0047 Volkswagen Golf VIII Variant engine ther; ABAKUS 053-025-0050 Volkswagen Golf VIII Variant engine ther; ABAKUS 123-00-165AL VW Polo Variant cylinder head |
| walk | interior-comfort | exhaust | 2.15 | 0.465 | 8 | AS 25152 Hyundai Kona OS catalytic converter; Cap Rr Seat Walk In; CITROËN C4 I Box Body / Hatchback (LR_) Rear silencer WALKER |
| yeti | interior-comfort | body-exterior | 1.16 | 0.161 | 8 | ABAKUS 120-03-131 Skoda Yeti 5L abs sensor; ABAKUS 123-00-035 Skoda Yeti 5L cylinder head; ABAKUS 130-003-073 Skoda Yeti 5L window regulator |
| בגל | gearbox | wheels-bearings | 2.41 | 0.447 | 8 | 315MH חיישן לחץ אויר בגלגל ת; 433MH חיישן לחץ אוויר בגלגל ת; 433MH ת NLS חיישן לחץ אוויר בגלגל |
| דאשבורד | fuel-air | interior-comfort | 1.5 | 0.4 | 8 | Leapmotor Part 000000000052173812; Leapmotor Part 000000000052237590; Leapmotor Part 000000000052237597 |
| השהיה | merchandise | engine | 1.4 | 0.412 | 8 | אוטומט השהיה לדלתות; ווסת השהיה למצתי להט; חיישן השהיה להצתה |
| לחיבור | electrical | body-exterior | 1.22 | 0.282 | 8 | OEM Part - גומיה לחיבור עמוד דלת; OEM Part - מבודד לחיבור כנף אחורית; OEM Part - 'פחית לחיבור שמשה קד |
| לקינ | clutch-drivetrain | wheels-bearings | 1.17 | 0.368 | 8 | GENESIS 82850T6020; LOJNE לקינ ELIA כנף אח' ש; LOJNE לקינ ELIA כנף קד' ש |
| לרצועת | safety-systems | belts-chains | 5.1 | 0.652 | 8 | 0I)F10 מותחן לרצועת מנוע; 30/F20 מותחן לרצועת מנוע; Belt Tensioner |
| מנג | audio-electronics | body-exterior | 2.6 | 0.494 | 8 | #250 מנגנ' הרמת שמש'אח'ימ; Abs Hydraulic Assembly; ABS מחשב למנגנון |
| קיבוע | fuel-air | body-exterior | 1.85 | 0.25 | 8 | Locating Fixture 813010; MOUNTING PIN-EHCU; OEM בורג קיבוע למסילות כיסא קדמי |
| תרסיס | fluids | interior-comfort | 1.33 | 0.291 | 8 | 012111; ARNAV MOTUL 102998 E3 400ML; ARNAV MOTUL 102999 |
| broch | merchandise | air-conditioning-heating | 1.08 | 0.334 | 7 | 09 Borrego Brochure; 13 Optima Brochure; 13 Sportage Brochure |
| compar | accessories | air-conditioning-heating | 1.23 | 0.263 | 7 | 6 Series Embroidered Luggage Compartment Mat; Adapter Lead Engine Compart 611016; AIC 58155 Bonnet Lock Engine Compartment, Front |
| ewo | service-general | brakes | 1.68 | 0.281 | 7 | 1997 DAEWOO LEGANZA ABS CONTROL UNIT 0265218521; 1997 DAEWOO NEXIA ABS Pump/Modulator; 1x Brake Pads Front for Daewoo Nubira 97- |
| excel | suspension-steering | interior-comfort | 1.16 | 0.202 | 7 | 225 55r17 97y Goodyear Excel 369041; 245 40r19 98y Excellence Bm 369041; 245 55 R17 102v Excellence R 369041 |
| fillister | service-general | engine | 2.97 | 0.737 | 7 | Combi Fillister Head Self T 889522; Csk Fillister Head Srew A13100b; Csk Fillister Head Srew A99v00 |
| gift | merchandise | air-conditioning-heating | 1.57 | 0.449 | 7 | 25 Service Gift Card; Christmas Cards Gift Tags; Collapsible Gift Box Size L |
| hollow | service-general | fasteners | 16.25 | 0.793 | 7 | 15.0mm Dowel - Hollow - For Transfer Box on Defender, Range ; Escutcheon Hollow Bolt; HOLLOW BOLT |
| htr | air-conditioning-heating | interior-comfort | 7.34 | 0.638 | 7 | Ashtray; ASHTRAY-1224102; ASHTRAY-6801390 |
| kalos | suspension-steering | brakes | 1.76 | 0.265 | 7 | 1810251 BRAKE LIGHT SWITCH STOP EPS FOR DAEWOO KALOS,NUBIRA,; 1x Brake Caliper Front Left for DAEWOO/CHEVROLET AVEO, KALOS; 1x Brake Caliper Front Right for DAEWOO/CHEVROLET AVEO, KALO |
| kilen | suspension-steering | body-exterior | 2.45 | 0.705 | 7 | Audi 100 Avant C3 Tailgate strut KILEN 412009; Audi 100 C4 Avant body KILEN 412011; AUDI A2 (8Z0) Tailgate strut KILEN 412025 |
| moog | service-general | suspension-steering | 5.53 | 0.834 | 7 | 147 Panorama Track rod end MOOG FI-ES-4079; Arena Van Ball Joint MOOG RE-BJ-4271; Audi A6 C8 Anti-roll bar link MOOG AU-LS-10678 |
| number | service-general | engine | 1.03 | 0.263 | 7 | ABAKUS 038-11-900 Number plate light both sides, without bul; ABAKUS 441-2112N-UE Number plate light both sides, without b; ABAKUS 446-2101R-WQ Number plate light both sides, with bulb |
| outdoor | accessories | body-exterior | 35.0 | 0.864 | 7 | Bracket For Outdoor Temperature Sensor M00666 13314891 00; Car Cover Turbo Outdoor; Cover F Outdoor Temperat Di C629900 |
| reinhoch | service-general | suspension-steering | 2.78 | 0.598 | 7 | BMW E23 suspension REINHOCH RH07-3013; Chrysler 300c LX steering REINHOCH RH01-6011; Citroen C25 280 Engine mount REINHOCH RH11-2138 |
| remove | service-general | wheels-bearings | 1.27 | 0.188 | 7 | Adhesive Strip Remover Vas 6349; Air Vent Remover; Autoglym Active Insect Remover (500ml) |
| stacked | merchandise | air-conditioning-heating | 2.29 | 0.696 | 7 | Bmw Mms Stacked Logo Tee 2 999999; Bmw Mms Stacked Logo Tee 2 B 999999; Bmw Mms Stacked Logo Tee L P 9917800001 |
| syste | accessories | electrical | 1.04 | 0.223 | 7 | 4MAX 0219-01-254355P Exhaust System Ø: 66mm; 4MAX 0219-14-0016P Exhaust System Centre; 4MAX 0219-14-0140P Exhaust System |
| technik | service-general | suspension-steering | 1.85 | 0.319 | 7 | 1000-Series Saloon Track rod end MOTOTECHNIKA 16-PKZ-03; 1000-Series Saloon Track rod end MOTOTECHNIKA 16-PKZ-04; Advanced Off Road Technik Paket Cayenne |
| uesivorte | gearbox | body-exterior | 1.36 | 0.556 | 7 | UESIVORTER מראה; UESIVORTER מראה חיצונית; UESIVORTER מראה ימין |
| xeh | service-general | fasteners | 1.09 | 0.431 | 7 | 001 XEH SIV בורג; 01 XEH SIV בורג; 01 XEH SIV בורג לבולם |
| yakima | accessories | air-conditioning-heating | 3.33 | 0.588 | 7 | Affiliated Yakima Reg Fatcat Evo 6 Ski Carrier; Affiliated Yakima Reg Frontloader Upright Bike Rack; Affiliated Yakima Reg Hitchski Hitch Mount Ski Adapter |
| אימובי | accessories | electrical | 1.46 | 0.419 | 7 | ANTITHEFT FLASR; COIL,ANTENNA-IMMOVI; HYUNDAI 954012L200 |
| אקטי | interior-comfort | electrical | 1.12 | 0.206 | 7 | Active stabiode; ACTUATOR-FLOW CONTROL; BODY(L),DOOR MIRROR |
| התראה | safety-systems | electrical | 3.38 | 0.551 | 7 | HYUNDAI 95740P6000; HYUNDAI 97950DR000YGU; L תושבת פיקוד התראה הולכי רגל ט |
| לגז | air-conditioning-heating | electrical | 2.0 | 0.333 | 7 | CCA ELADEP דוושה לגז; KS853C; KS853SET |
| מהירויות | electrical | air-conditioning-heating | 5.75 | 0.697 | 7 | *0/63 נגד מהירויות למזגן; 16 נגד מהירויות ספארק מ; Blower Motor Resistor |
| עקר | merchandise | engine | 2.33 | 0.467 | 7 | SMART 8895078269D17; T שירט HYPE+עקרב חאקי-S; מדבקה מ.מנוע עקרב אדום (5HS) אבארט 500 |
| adg | suspension-steering | body-exterior | 3.23 | 0.355 | 6 | Automatic Boot Badge; Badge; Badge 2000 Mk2 |
| asher | air-conditioning-heating | fasteners | 1.36 | 0.382 | 6 | 3RG 88814 Windscreen washer pump; 9-5 2006 to 2010 all models – Headlamp Washer Nozzle Right H; ACKOJA A32-08-0004 Headlight washer jet Left |
| assortment | service-general | fuel-air | 1.14 | 0.381 | 6 | ALCA 508000 Assortment, hose clamps; AMiO 02548 Assortment, hose clamps; AMiO 04125 Assortment, hose clamps |
| asymmetric | wheels-bearings | air-conditioning-heating | 5.33 | 0.821 | 6 | Eagle F1 Asymmetric; Eagle F1 Asymmetric 2 N; Eagle F1 Asymmetric 3 R |
| brief | service-general | air-conditioning-heating | 18.6 | 0.921 | 6 | Briefcase; Briefcase Ba; Briefcase Black |
| cantrail | service-general | body-exterior | 1.44 | 0.397 | 6 | Beading on Cantrail; Body Cantrail Seals - Pair; Cantrail |
| caution | safety-systems | body-exterior | 1.03 | 0.477 | 6 | A C Caution Label; Back Up Camera Label Safety Caution; Caution |
| cielo | suspension-steering | brakes | 1.78 | 0.281 | 6 | 0 986 475 032 DRUM WHEEL BRAKE CYLINDER REAR BOSCH FOR DAEWO; 1X BRAKE SHOE FITS: CHEVROLET EPICA LACETTI NUBIRA; DAEWOO A; 1X BRAKE SHOE FITS: CHEVROLET LACETTI LANOS; DAEWOO ARANOS C |
| compensation | service-general | fasteners | 5.86 | 0.788 | 6 | Compensation Washer; Cooling Water Compensation T C411410; Forge Performance Intake Pressure Compensation Valve |
| cycle | accessories | engine | 1.06 | 0.216 | 6 | a c clutch cycle switch; A C Clutch Cycle Switch; ACTUATOR,RECYCLE & F |
| drivi | electrical | clutch-drivetrain | 1.43 | 0.258 | 6 | Alternator Driving Belt - V Belt; Alternator Driving End Bracket Circlip; Base Mounted Driving Lamps - Chrome - 5 inch Diameter - Pair |
| espero | suspension-steering | brakes | 4.15 | 0.483 | 6 | 04650 BRAKE SHOE SET KIT REAR LPR FOR DAEWOO ESPERO,PRINCE 2; 06800 BRAKE SHOE SET KIT REAR LPR FOR DAEWOO NUBIRA,NEXIA,ES; 1810050 BRAKE LIGHT SWITCH STOP EPS FOR DAEWOO NEXIA,ESPERO, |
| ign | electrical | engine | 1.03 | 0.38 | 6 | 21 Inch Cayenne Turbo Design Summer; 21 Inch Panamera Exclusive Design Sport Winter; 22 Inch 911 Turbo Design Summer Wheel And Tire |
| johns | body-exterior | lighting | 1.26 | 0.494 | 6 | AUDI A6 C8 Allroad (4AH) body JOHNS 13 21 37-81; Audi A8 D3 body JOHNS 13 12 38-83; BMW E34 Touring Side indicator JOHNS 20 07 22-5 |
| klej | suspension-steering | brakes | 1.45 | 0.236 | 6 | 2x Brake Discs Pair Vented fits DAEWOO ESPERO KLEJ Front 1.5; 2x Brake Drums (Pair) fits DAEWOO ESPERO KLEJ 1.5 Rear 93 to; 2x Brake Drums (Pair) fits DAEWOO ESPERO KLEJ 1.8 Rear 95 to |
| kme | service-general | clutch-drivetrain | 3.5 | 0.656 | 6 | KME 823 000 001 Reducer repair kit; KME 823 000 003 Reducer repair kit; LPR KME1046 Mercedes Vito W447 cv joint |
| loctite | fasteners | body-exterior | 1.29 | 0.36 | 6 | Adhesive Loctite 577 759501; Loctite 242e; LOCTITE 262 |
| luggag | accessories | body-exterior | 1.58 | 0.383 | 6 | 6 Series Embroidered Luggage Compartment Mat; All Weather Luggage Compartment Mat; bar luggage door hi |
| mal | fasteners | engine | 1.44 | 0.304 | 6 | 200 Saloon belt / chain drive OPTIMAL 0-N2048; 5 16 Male Pipe; 780 Top strut mount bearing OPTIMAL F8-6267 |
| mitt | accessories | gearbox | 3.22 | 0.613 | 6 | 2 key transmitter; 3 key transmitter; 9-3SS 03′ on – Remote Key Fob/Transmitter – Genuine |
| north | merchandise | air-conditioning-heating | 3.1 | 0.539 | 6 | adapter assembly fuel filler lid rp46p premium northern ligh; Air Intake Ducting for Freelander 1 2.5 V6 Petrol - North Am; Ambtn Polo North Face, 2 |
| overhau | service-general | engine | 1.08 | 0.345 | 6 | A/Trans Overhau; automatic transmission overhaul kit; Automatic Transmission Overhaul Kit |
| pag | fluids | air-conditioning-heating | 3.88 | 0.748 | 6 | 250ML שמן מדחסים PAG46R-1234YF; AIC 54452 Mercedes Pagode W113 tie rod; Airstal 10-0063 Air conditioning compressor Refrigerant: R13 |
| ractis | suspension-steering | electrical | 1.33 | 0.226 | 6 | A.B.S. 17750 TOYOTA Ractis I (XP100) brake discs; A.B.S. 31398 TOYOTA Vios / Yaris II Saloon (XP9) abs sensor; ACKOJA A55-99-0002 TOYOTA Ractis I (XP100) engine thermostat |
| spreader | service-general | fasteners | 5.0 | 0.652 | 6 | Body Shop Spreader P; Brake Cylinder Seal Spreader; Disc Brake Caliper Piston Spreader |
| textu | interior-comfort | body-exterior | 1.24 | 0.467 | 6 | Bumper Appliqu Rear Textured; Elbow For Textu; GRILLE, TEXTURE |
| topex | service-general | fasteners | 1.03 | 0.402 | 6 | TOPEX 37D501 Hose Clamp; TOPEX 37D502 Hose Clamp; TOPEX 37D503 Hose Clamp |
| trw | suspension-steering | brakes | 1.09 | 0.458 | 6 | 900 T / E Brake caliper TRW BHV171E; Audi 100 Coupe Brake pad set TRW GDB279; AUDI e-tron GT Saloon Brake pad set TRW GDB1774 |
| גבר | merchandise | brakes | 2.44 | 0.527 | 6 | 01-02 מגבר בלם אקורד אקורד אנגלי; '20 אטם למגבר בלם פוקוס; 48 מכנס לרוכב לגבר |
| דיודה | filters | electrical | 4.5 | 0.643 | 6 | 5 דיודה ב; DIODE ASSY; DIODE ASY |
| הצק | merchandise | suspension-steering | 1.2 | 0.375 | 6 | bumper cover heat shield; cowl side panel; ENITAL הצק EF פח דופן אח |
| לגוז | gearbox | fasteners | 1.67 | 0.357 | 6 | NUT - HEX.; NUT-HEX.-FLANGD; RENAULT RE7703033165 |
| סולונואיד | electrical | engine | 1.49 | 0.365 | 6 | GENESIS 211502R000; HYUNDAI 463132F300; RETAINER |
| ספירל | body-exterior | suspension-steering | 2.59 | 0.552 | 6 | Clock Spring; EH TROSSER ספירלים; LR/JAG Part - קיט הסבה והגבהה מכריות אוויר לקפיצים ספירליים  |
| פלק | electrical | engine | 1.37 | 0.555 | 6 | 2010 CRV רפלקטור אחורי שמאלי; DEFLECTOR-AIR; HANDLE ASY |
| פרסום | merchandise | lighting | 1.44 | 0.382 | 6 | KIT LAVAZZA; Leapmotor Part 000000006001099229; Leapmotor Part 000000006001099310 |
| ריצפה | interior-comfort | body-exterior | 1.41 | 0.455 | 6 | Connecting mode; COUVERCLE מכסה לפח ריצפה; Extension Panel |
| adt | suspension-steering | brakes | 1.23 | 0.201 | 5 | 10x1 2 Hexwashrheadt; BLUE PRINT ADT30102 Sump plug washer Aluminium; BLUE PRINT ADT30302 Windscreen washer pump with seal ring |
| autoglym | fluids | body-exterior | 2.43 | 0.378 | 5 | Autoglym Active Insect Remover (500ml); Autoglym Aqua-Dry Synthetic Chamois Leather; Autoglym Autofresh (500ml) |
| automat | service-general | gearbox | 9.05 | 0.69 | 5 | 9-5 2002 to 2010 all automatic transmission models – Drivesh; 9-5 99′-10′ RHD Automatic Brake Servo Vacuum Hose – Alternat; ABAKUS 004-017-0046 Engine radiator Automatic Transmission |
| bellcrank | suspension-steering | air-conditioning-heating | 1.89 | 0.425 | 5 | Bellcrank; BELLCRANK; bellcrank-6970148010 |
| butadiene | service-general | engine | 19.6 | 0.84 | 5 | 3RG 84219 Inlet manifold gasket NBR (nitrile butadiene rubbe; AJUSA 11089500 Rocker cover gasket NBR (nitrile butadiene ru; AJUSA 14070800 Oil sump gasket NBR (nitrile butadiene rubber |
| centering | service-general | clutch-drivetrain | 19.83 | 0.832 | 5 | 3RG 22102 Centering Pin, clutch; ASTA A-17UCA Centering Tool Set, clutch; ASTA S-2110S Centering Tool, clutch plate |
| departure | safety-systems | electrical | 14.43 | 0.828 | 5 | Departure Protection Cover I; Lane Departure System; lane departure system camera |
| desk | accessories | brakes | 1.03 | 0.429 | 5 | Acrylic Desk Presenter; Bodykit Lft Sideskrt; Chrome Desk Tray |
| digit | electrical | air-conditioning-heating | 2.5 | 0.365 | 5 | 1L Selenia Digitek 0W40 9.55535-Z2; 1L SELENIA DIGITEK 0W40 9.55535-Z2; 2L Selenia Digitek 0W40 9.55535-Z2 |
| dometic | accessories | air-conditioning-heating | 1.36 | 0.517 | 5 | Cooler Cover Dometic Cfx3 Pc45; dometic cfx3 pc35 cooler cover; Dometic Cfx3 Pc35 Cooler Cover |
| engin | electrical | engine | 2.0 | 0.457 | 5 | 147 Panorama Rocker cover gasket AJUSA 11010100; 3RG 48721 Valve, engine block breather; 3RG 81653_3 NISSAN NV300 Van (X82) engine mount |
| exhst | exhaust | engine | 2.69 | 0.449 | 5 | 4run Exhst; 4run Exhst Tip Clmp; 86 Exhst Systm Pol S |
| fare | service-general | suspension-steering | 1.17 | 0.177 | 5 | Adapter Lead Fanfare Horn 611016; Bumper Diffarental; Fanfare |
| gbp | electrical | suspension-steering | 2.55 | 0.279 | 5 | ABAKUS 140-01-252 Ford Mondeo BFP power steering pump; ABAKUS 431-1106L-LD-EM Ford Mondeo GBP headlights; A.B.S. 200020 Ford Mondeo MK1 Estate wheel bearing |
| goom | service-general | engine | 1.87 | 0.312 | 5 | GOOM AGI-0001 Crankshaft gear; GOOM AGI-0002 Crankshaft gear; GOOM AGI-0017 Crankshaft gear |
| guarantee | service-general | merchandise | 13.0 | 0.918 | 5 | ABS Sensor Rear 60539 Intermotor Wheel Speed 96205924 Genuin; ABS Sensor Rear AB1941 Fuel Parts Wheel Speed Genuine Top Qu; ABS Sensor Rear LAB550 Lemark Wheel Speed 96205924 Genuine Q |
| hcc | fasteners | clutch-drivetrain | 4.5 | 0.643 | 5 | EXEDY HCC506 Clutch Pressure Plate; EXEDY HCC542 Clutch Pressure Plate; EXEDY HCC543 Clutch Pressure Plate |
| ipod | audio-electronics | wheels-bearings | 1.79 | 0.569 | 5 | Alfa e-Parts AF03961 Brake Light Switch with tripod; Audio Ipod Integration; BIRTH 3383 Tripod hub, drive shaft Front Axle Left, Front Ax |
| joints | clutch-drivetrain | suspension-steering | 6.24 | 0.765 | 5 | Adams Driveshaft CJ Front Slip N Stub 1310 Driveshaft Extrem; Adams Driveshaft CJ Front Slip N Stub 1310 Driveshaft Heavy ; Adams Driveshaft CJ Rear Slip N Stub 1310 Driveshaft Extreme |
| knurled | service-general | fasteners | 8.2 | 0.872 | 5 | BOLT - KNURLED; Knurled Bolt; Knurled Bolt 889520 |
| ktp | suspension-steering | belts-chains | 4.02 | 0.74 | 5 | Camry V20 damping JAPANPARTS KTP-0102; FIAT Croma I Hatchback (154) Dust cover kit, shock absorber ; Hyundai Atos MX Dust cover kit, shock absorber JAPANPARTS KT |
| lgr | service-general | brakes | 1.65 | 0.246 | 5 | 555 SB-4842 NISSAN Elgrand III (E52) suspension ball joint; ABAKUS 141-01-216 NISSAN Elgrand II (E51) wheel bearing; A.B.S. 17698 NISSAN Elgrand III (E52) brake discs |
| luk | lighting | clutch-drivetrain | 10.77 | 0.88 | 5 | 4LT שמן מנוע LukOIL 5W30 SN בקרטון 4; Daewoo Lanos - Luk LFW459; INA 462 0146 10 Repair Set, manual transmission |
| mascot | body-exterior | filters | 1.17 | 0.154 | 5 | 3A17 brake caliper left front RENAULT MASCOTT CAJA CHASIS 19; ABAKUS 551-1143R-LD-EM RENAULT Mascott Van (FH_) headlights; A.B.S. 17884 RENAULT Mascott Van (FH_) brake discs |
| nbr | service-general | engine | 19.87 | 0.81 | 5 | 3RG 84219 Inlet manifold gasket NBR (nitrile butadiene rubbe; AJUSA 11089500 Rocker cover gasket NBR (nitrile butadiene ru; AJUSA 14070800 Oil sump gasket NBR (nitrile butadiene rubber |
| odp | service-general | engine | 2.43 | 0.654 | 5 | Girlshoodpur; GOOM ODP-0001 Sump plug washer Copper; GOOM ODP-0005 Sump plug washer Elastomer |
| phv | hybrid-ev | suspension-steering | 1.33 | 0.222 | 5 | A.B.S. 212179 Toyota Prius Phv control arm; A.B.S. 212242 Toyota Prius W50 control arm; A.B.S. 32220 Toyota Prius W50 abs sensor |
| preparation | accessories | body-exterior | 1.75 | 0.467 | 5 | Activity Key Vehicle Preparation; Child Car Seat Preparation Left Hand Dri; Child Car Seat Preparation Right Hand Dr |
| receptac | electrical | safety-systems | 2.32 | 0.664 | 5 | box fr ash receptac; HOFFER 8193217 Coolant Flange without sensor, without Stud R; Land Rover Seat Belt Receptacle |
| restraint | safety-systems | engine | 1.45 | 0.305 | 5 | Bracket, Child Restraint; Child Restraint Anchor; child restraint anchor plate |
| rot | interior-comfort | body-exterior | 1.96 | 0.402 | 5 | 9-3 98′-02′ & NG900 94′-98′ Rotor arm – Genuine Saab; ABAKUS 121-02-052 Throttle body; Absorber Rollover Protection 519902 |
| ruetcelfe | fasteners | body-exterior | 3.5 | 0.5 | 5 | RUETCELFED דופן ק ימין; RUETCELFED טבעת לדרמים אח; RUETCELFED מגן רוח |
| scorpio | suspension-steering | interior-comfort | 1.41 | 0.187 | 5 | 235 55r20 Pi Scorpion Ms Xl; 285 45r20 Pi Scorpion Ms Xl; A.B.S. 15788 Ford Scorpio Estate brake discs |
| serration | service-general | fasteners | 11.0 | 0.898 | 5 | bolt serration; Bolt Serration; Int Serration Flange Screw |
| stm | suspension-steering | interior-comfort | 1.37 | 0.434 | 5 | ABAKUS 135-03-009 Switch, mirror adjustment; ABAKUS 2702M01 Wing mirror for manual mirror adjustment, Con; ABAKUS 3905M01 Wing mirror Convex, for electric mirror adjus |
| suppression | electrical | air-conditioning-heating | 1.45 | 0.348 | 5 | BERU EVL088 Distributor rotor arm Interference Suppression 1; BERU EVL4/6-O Distributor rotor arm Interference Suppression; Interference Suppression Ban 129900 |
| swgautomotive | service-general | exhaust | 1.45 | 0.503 | 5 | SWGAutomotive 24000100316 Oil sump plug Steel, Spanner Size:; SWGAutomotive 2401415180 Oil sump plug Steel, Spanner Size: ; SWGAutomotive 24100100416 Oil sump plug Steel, Spanner Size: |
| tal | fluids | exhaust | 1.71 | 0.326 | 5 | 1 Set Of Trim Foil White Gold Metallic; 9000 1989 to 1998 w/ separate CAT (see descr) – Catalyst Typ; 9-5 ’98-03 3.0 V6 Petrol Models(B308E) – Catalytic Converter |
| toggle | interior-comfort | electrical | 2.0 | 0.35 | 5 | 2 Way Classic Smiths Style Toggle Override Switch; CLAMP,TOGGLE,GRAINGER 5E659; Cooling Fan Override Switch - Toggle Style |
| too | service-general | engine | 1.12 | 0.195 | 5 | 12v 10w Festoon; 7 32 Carbide Burr Nh Tool; ABAKUS 142-01-509 Timing chain kit with chain tensioner, Sim |
| transistor | electrical | gearbox | 3.0 | 0.682 | 5 | Transistor; transistor assembly; Transistor Assembly |
| waiting | service-general | air-conditioning-heating | inf | 1.0 | 5 | Pko Waiting For Delivery Mailing Porsch; Taycan Sales Round Mailing Waiting Fo; Waiting For Delivery Mailing Porsche 35 |
| wartungsbroschur | merchandise | air-conditioning-heating | 5.4 | 0.844 | 5 | 911 Garantie Und Wartungsbroschure A; Cayenne Garantie Und Wartungsbroschur; Garantie Und Wartungsbroschure 911 Gt2 |
| wool | merchandise | interior-comfort | 2.57 | 0.529 | 5 | Black Wool Pad; Booster Seat Cushion Wool; Carpet Set Tan Wool |
| אחוריים | body-exterior | fuel-air | 1.5 | 0.243 | 5 | 5 .צילונים לחלונות אחוריים; Leapmotor Part 000000000052181344; LR/JAG Part - זוג קפיצים אחוריים מתוצרת TERRAFIRMA בעלי כושר |
| ביטחונית | safety-systems | engine | 1.5 | 0.25 | 5 | AM EHPARGA ביטחונית לידית; Clip; EXA NIERF ביטחונית |
| בלו | interior-comfort | engine | 3.86 | 0.57 | 5 | 2.0 מכסה פולי אלט אקובלו; 4 כבל מע' בלום א"ח שמאלי 4 ר; ASA-Bolt |
| גריז | fluids | body-exterior | 2.49 | 0.465 | 5 | 0.5KG גריז מולי פואל PBC; 12pc BOX - GADUS S2 V220AC 2; 1KG גריז תקן MR2 |
| זוית | engine | electrical | 2.05 | 0.368 | 5 | 3 DROCCA זוית לנשם; Engine Coolant Bypass Hose; HOSE WATER BY PASS |
| חרום | safety-systems | electrical | 6.75 | 0.664 | 5 | Accumulator ode; COMMUTATEUR מתג אורות חרום; DETR RETNI מתג חרום |
| טישרט | merchandise | engine | 2.2 | 0.55 | 5 | BMW M T-shir; M דגם M טישרט גברים כחול; T-Shirt, Darshirt |
| כתום | accessories | body-exterior | 1.39 | 0.315 | 5 | 7 משקף כתום סיסטם; BMW כובע כתום; Bridge, high |
| לגריז | clutch-drivetrain | body-exterior | 3.75 | 0.652 | 5 | SEAL; SEALA-GREASE; כפה לגריז נבה אח' |
| לניתוק | safety-systems | electrical | 1.75 | 0.368 | 5 | 350 ABS צמת חוטים לניתוק; Bulb Indicator Air Bag; BULB,INJECTOR-AIR BU |
| לשעונים | interior-comfort | electrical | 2.33 | 0.583 | 5 | בסיס לשעונים; זכוכית לשעונים S80 07-08; כפתור לשעונים |
| מגדש | electrical | fuel-air | 8.92 | 0.806 | 5 | 5.0 משופץ D F15 מגדש טורבו; BOLT; BRACKET |
| מכונית | merchandise | engine | 1.0 | 0.389 | 5 | דגם מכונית AKK414 S; דגם מכונית ג'ימני; מכונית ילדים מיאמי כחול |
| פיקסו | air-conditioning-heating | electrical | 2.67 | 0.571 | 5 | בורג פיקסו; וו גרירה C4 פיקסו B78; להב מפתח C4 פיקסו |
| תפש | filters | engine | 9.37 | 0.83 | 5 | 316 F-30 שסתום התפשטות; Electric exp; Electric expode |
| ami | suspension-steering | electrical | 1.29 | 0.259 | 4 | AISIN X6F925S FIAT 124 Familiare (124) brake discs; AMiO 01004 Brake / tail light bulb BA15s, 24V, 10W, R10W; AMiO 01008EAN Fuel cap |
| antara | suspension-steering | interior-comfort | 4.66 | 0.504 | 4 | ABAKUS 442-1154LMLD-EM Opel Antara 07 headlights; AJUSA 16088200 Opel Antara 07 fuel pump; ALANKO 10900915 Opel Antara 07 turbocharger |
| automati | service-general | gearbox | 9.05 | 0.69 | 4 | 9-5 2002 to 2010 all automatic transmission models – Drivesh; 9-5 99′-10′ RHD Automatic Brake Servo Vacuum Hose – Alternat; ABAKUS 004-017-0046 Engine radiator Automatic Transmission |
| basecoat | body-exterior | air-conditioning-heating | 12.2 | 0.772 | 4 | basecoat-2581f2bc21; basecoat for 00057; Basecoat For 00057 |
| bass | audio-electronics | air-conditioning-heating | 1.77 | 0.411 | 4 | A/C subassembly; Auxiliary Amplifier For Bass Loudspeaker; Bass Loudspeaker |
| bike | accessories | electrical | 2.61 | 0.319 | 4 | Active E Bike Li Ion Battery 808092; Affiliated Yakima Reg Holdup 2 Hitch Mount Bike Rack; Affiliated Yakima Reg Ridgeback 4 Hitch Mount Bike Rack |
| bill | service-general | body-exterior | 1.83 | 0.407 | 4 | Antenna Assembly Telematics Nh689m Billet Silver Metallic; Billet Oil Cap; Elite Billet Alum Hitch Cover Armada |
| boxst | service-general | body-exterior | 1.14 | 0.159 | 4 | 2017 718 Boxster Cayman French Om; 2017 718 Boxster Cayman French Qrg; 2018 718 Boxster Cayman Emissions Brochu |
| bracelet | accessories | air-conditioning-heating | 139.0 | 0.986 | 4 | Bracelet; Bracelet 911 Turbo; Bracelet Grey Black |
| breathe | fuel-air | engine | 3.15 | 0.641 | 4 | 3/4" diameter Breather Pipe; 3RG 48721 Valve, engine block breather; 3RG 81116 Valve, engine block breather |
| burgundy | accessories | merchandise | 1.33 | 0.399 | 4 | A Pillar Windscreen Trims - Comes as a Pair - In Burgundy Le; Armrest Assembly L Front Door Yr351l Leather Burgundy Brown; Armrest Assembly L Rear Door Yr351l Leather Burgundy Brown |
| carrara | interior-comfort | electrical | 2.33 | 0.449 | 4 | 1 Set Trims Carrara White Metallic; Airbag Steering Wheel Carrara Grey; Airbag Unit Carrara Grey |
| carre | service-general | interior-comfort | 1.47 | 0.332 | 4 | 20 21 Inch Carrera Classic Summer; 20 21 Inch Carrera S Summer Wheel And Tire; 20 21 Inch Carrera S Winter Wheel And Tire |
| cayma | service-general | air-conditioning-heating | 2.45 | 0.308 | 4 | 2017 718 Boxster Cayman French Om; 2018 718 Cayman Gts Emissions Brochure; 20 Inch 718 Cayman Gt4 Set Of Wheels |
| chem | fluids | gearbox | 1.91 | 0.522 | 4 | 2 Part Chemical Re; Axle Heat Shield Attachement Bracket; Becm Chem P27 |
| cher | service-general | body-exterior | 1.86 | 0.245 | 4 | 0 986 475 916 DRUM WHEEL BRAKE CYLINDER PAIR BOSCH 2PCS FOR ; 2x Brake Discs Pair Solid For Jeep Cherokee 2.8 CRD Rear 521; 2 X FEBI BILSTEIN BRAKE DISC REAR CHRYSLER JEEP 200 CHEROKEE |
| chk | service-general | belts-chains | 4.38 | 0.609 | 4 | Bolt Flg Chk M8; CHKER AY-FR DR; CHKER AY R DR |
| chorus | fluids | suspension-steering | 1.17 | 0.269 | 4 | A.B.S. 9335 HYUNDAI Casper SUV (AX1) handbrake; BOSCH 1 987 470 600 HYUNDAI Grandeur I (L) brake caliper rep; COMMA BF41L HYUNDAI Chorus II Minibus (GA17) brake fluid |
| chra | service-general | electrical | 1.38 | 0.525 | 4 | 1 25 In Schrader Vlve Cas; 2 5 In Schrader Vlve Case; 2 In Schrader Vlve Case 5 |
| circ | service-general | electrical | 1.48 | 0.326 | 4 | 16mm Circlip for Differential Gears on Rover Axles and Clutc; Air Circulation; Amplifier Trap Circuit C651022 |
| clean | service-general | body-exterior | 1.1 | 0.245 | 4 | 3m Wheel Surface Cleaner Prep 2000 16oz; 3RG 86209 Windscreen washer jet both sides, for windscreen c; 3RG 88090 DPF cleaner |
| collared | accessories | fasteners | 4.5 | 0.776 | 4 | 12 Point Collared Bolt; Collared Uv Protection; Double Collared Stud |
| comfortred | interior-comfort | air-conditioning-heating | 3.75 | 0.75 | 4 | Assurance Comfortred; Assurance Comfortred To |
| conrod | engine | wheels-bearings | 3.33 | 0.678 | 4 | 1 Set Conrod Bearing; Bluebird 510 Conrod Bearing Set KOLBENSCHMIDT CB-1044GP 0.25; Bluebird 510 Conrod Bearing Set KOLBENSCHMIDT CB-1044GP 0.50 |
| csd | service-general | air-conditioning-heating | 2.75 | 0.635 | 4 | Csd; MGA CSD61817 Serpentine belt; S/S to Gja1100bcsdc |
| disinf | fluids | air-conditioning-heating | 1.33 | 0.5 | 4 | 55 Gal Pump For Disinfectant Sanitizer; Bioesque Disinf 55 Gal Drum; Botanical Disinfectant Solut 831530 |
| dna | gearbox | electrical | 1.0 | 0.225 | 4 | 2012 Sedna Accy Broc; AG UAEDNAB פלסטיק לדלת; AVA COOLING SYSTEMS DNA2297 Engine radiator Aluminium, Mecha |
| dtm | electrical | cooling | 41.17 | 0.918 | 4 | Dc Dtm Lock Wed; Dc Dtm Plug 12p; Dc Dtm Plug 2pi |
| edt | fluids | electrical | 2.0 | 0.526 | 4 | Bmw M 1972 Edt 50 Ml 809120; Bmw M 1972 Edt Tester 100 Ml 809120; Bmw M 1985 Edt 50 Ml 809120 |
| ended | service-general | fluids | 1.0 | 0.135 | 4 | 6 Inch Lift Kit Ford F100/F150 80-96 2WD Extended Cab NX2 Ni; ABAKUS 141-01-428 Chevy S10 Extended Cab wheel hub; A.B.S. 16364 CHEVROLET S10 II Standard Cab Pickup brake disc |
| err | service-general | suspension-steering | 1.07 | 0.197 | 4 | 555 SB-N281 NISSAN Xterra (N50) suspension ball joint; ABAKUS 014-025-0019 Mercedes C257 engine thermostat; ABAKUS 215-1139L-LD Nissan Terrano WD21 headlights |
| exibition | merchandise | body-exterior | 1.33 | 0.533 | 4 | Exibition Ja; Exibitionqui; Exibitionqul |
| flash | lighting | electrical | 1.33 | 0.479 | 4 | 12 volt Hazard / Flasher Relay; 2pk Led Flashlight; 9000 CS/CSE/Aero (5 Door) 1992 to 1998 – Flasher Relay |
| gene | fuel-air | wheels-bearings | 1.29 | 0.222 | 4 | 2019-2023 Dodge Ram 2500 - 3500 Diesel 5th Generation Front ; 2x BRAKE DISC 200.2529.20 FOR INFINITI NISSAN GENESIS DERQR2; A.B.S. 18231 Hyundai Genesis DH brake discs |
| ghibli | suspension-steering | brakes | 31.12 | 0.912 | 4 | 14-19 Maserati Ghibli M157 Front Right & Left Brembo Brake C; 1 piece Maserati Rear Brake Disc Fits Ghibli Levante 2017-20; 2013-2019 Maserati Ghibli Anti Lock ABS Brake Pump Module Un |
| grub | service-general | fasteners | 12.0 | 0.75 | 4 | Grub Screw; Grub Screw 9100g82gt3; Grub Screw Allen Head |
| hydrogen | hybrid-ev | fuel-air | 3.0 | 0.44 | 4 | Bracket Hydrogen De; Bracket Hydrogen In; Bracket Hydrogen Pu |
| iccu | electrical | air-conditioning-heating | 1.0 | 0.5 | 4 | GENESIS 364001XCB0A; Iccu Assy; Wiring Assy Iccu Gro |
| icm | electrical | wheels-bearings | 2.11 | 0.568 | 4 | Box Assy Icm Relay; Control unitode; Icm 7 Quot Display |
| idash | audio-electronics | fuel-air | 1.24 | 0.31 | 4 | 52mm iDash Stealth Pod Mounts for 15-19 GM 2500/3500 Dual Po; 52mm iDash Stealth Pod Mounts for 15-19 GM 2500/3500 Single ; Banks iDash Data Pro for use with OBDII CAN bus vehicles Sta |
| illumin | lighting | body-exterior | 3.82 | 0.521 | 4 | 1 Set Of Sill Trim Strips Illuminated; Bed Cargo Illumination; Bracket Illumination Lamp Lh |
| impala | suspension-steering | brakes | 1.38 | 0.232 | 4 | ABAKUS 141-01-244 CHEVROLET Uplander MPV wheel bearing; A.B.S. 16460 CHEVROLET Caprice Saloon brake discs; A.B.S. 16917 CHEVROLET Caprice Saloon brake discs |
| inac | service-general | body-exterior | 1.78 | 0.291 | 4 | 205 45r 16 Inac; 235 45r 17 Inac; Actuator Inac |
| inlay | interior-comfort | air-conditioning-heating | 2.0 | 0.516 | 4 | Bob Inlay; Centre Inlay Front Bumper 511270; INLAY-4320634 |
| instruction | service-general | air-conditioning-heating | 43.7 | 0.955 | 4 | Brief Instructions F01 F02 861017; Installation Instructions 861075; Instruction Notice Airbag C712010 |
| integra | suspension-steering | wheels-bearings | 1.02 | 0.19 | 4 | ABAKUS 038-10-870S Third brake light Centre, without bulb, w; ABAKUS 103-00-059-P Wiper arm set with cap, with integrated ; ABAKUS 103-00-077-C Wiper arm set with cap, with integrated  |
| integrated | accessories | wheels-bearings | 1.04 | 0.195 | 4 | ABAKUS 038-10-870S Third brake light Centre, without bulb, w; ABAKUS 103-00-059-P Wiper arm set with cap, with integrated ; ABAKUS 103-00-077-C Wiper arm set with cap, with integrated  |
| ion | wheels-bearings | suspension-steering | 1.08 | 0.197 | 4 | 9-3SS 2003 to 2012 all models – Front Right Hand Door Lock –; A.B.S. 220738 Volkswagen Atlas (CA1) suspension ball joint; A C Refrigerant Suction Hose |
| isofix | safety-systems | body-exterior | 2.62 | 0.586 | 4 | Base-isofix; Black ISOFIX trim; Button Isofix 529900 |
| itro | fuel-air | suspension-steering | 2.06 | 0.226 | 4 | AE CAM1027 CITROËN Relay II Platform / Chassis (250) camshaf; AIC 76456 Сitroën ZX N2 aerial; AJUSA 16097100 CITROËN C5 X III fuel pump |
| jogger | merchandise | brakes | 21.75 | 0.935 | 4 | 0 986 481 193 DRUM WHEEL BRAKE CYLINDER PAIR REAR BOSCH 2PCS; 0 986 481 193 DRUM WHEEL BRAKE CYLINDER REAR BOSCH FOR DACIA; 2024 DACIA JOGGER 1.6 PETROL HYBRID ABS PUMP CONTROL MODULE  |
| kep | service-general | fasteners | 1.14 | 0.261 | 4 | 10 24 Kep Nut; 10 24 Kep Nuts 18 8; 10 32 Kep Nut |
| kurgo | accessories | air-conditioning-heating | 2.2 | 0.478 | 4 | kurgo backseat barrier black; kurgo cargo cape charcoal; Kurgo Collaps A Bowl |
| lcs | hybrid-ev | fuel-air | 1.0 | 0.333 | 4 | Actuator3 43rd Lcsc; B L Stnls Stl Lcs Plt Infiniti Logo; Lcs Cup 0 40l 125m Sieve 751010 |
| lux | merchandise | filters | 1.33 | 0.247 | 4 | A.B.S. 36714 TOYOTA Hilux V Pickup brake pads; Airbag Unit Luxor Beige; All Weather Floor Mats Luxor Beige |
| mac | service-general | wheels-bearings | 1.13 | 0.161 | 4 | 2018 Macan Emissions Brochure; 20 Inch Macan S Winter Wheel And Tire Set; 20x8.5-Inch Multi-Spoke Wheel in Gloss Black with Machined A |
| magnetic | accessories | wheels-bearings | 1.06 | 0.335 | 4 | A1 Magnetic Hol; ABAKUS 141-03-111 Honda Odyssey RL3 wheel bearing; ABAKUS 141-03-111 Wheel bearing kit Front Axle, with integra |
| mhc | fasteners | air-conditioning-heating | 3.0 | 0.75 | 4 | S/S to Mhc2744aa; S/S to Mhc2862aa; S/S to Mhc3397aa10 |
| mineral | fluids | gearbox | 1.5 | 0.543 | 4 | Aceite Mineral 20w50; ARAL 14F73E Engine oil 15W-40, Mineral Oil, Capacity: 1l; Body Side Moldings Mineral Metallic |
| modul | cooling | electrical | 11.04 | 0.771 | 4 | ABAKUS 133-003-013 Cooling fan control module; ABAKUS 136-01-003 Lighting control module both sides; ABAKUS 136-01-006 Lighting control module both sides |
| night | accessories | body-exterior | 1.9 | 0.467 | 4 | 000 RR midnight blue אחת; Adapter Cable Night Vision 611016; Adhesive Carpet Midnight Blue |
| nippl | fasteners | fluids | 1.3 | 0.324 | 4 | 1/4 UNF Straight Grease Nipple; 1/8 BSP Straight Grease Nipple; 9000 (see descr. for apps.) Valve Cover Ventilation/Breather |
| panoramic | body-exterior | air-conditioning-heating | 1.48 | 0.274 | 4 | CHERY Tiggo 8 Pro Original Front Camera with 1 Million Pixel; CHERY Tiggo 8 Pro Original Front View Camera With 1 Million ; CHERY Tiggo 8 Pro Original Rearview Camera With 1 Million Pi |
| pcb | electrical | air-conditioning-heating | 3.0 | 0.443 | 4 | Aux In Module Pcb Housing; Bulb Pcb Wir G; Bulb Pcb Wir G Ass |
| pcg | service-general | fasteners | 2.75 | 0.5 | 4 | FEBEST 0199-PCGRJ120LH Handbrake cable Left Rear, for left-h; FEBEST 0199-PCGRJ120RH Handbrake cable Right Rear, for left-; FEBEST 0199-PCGRJ120RH Prado 120 handbrake |
| pdk | interior-comfort | suspension-steering | 1.33 | 0.296 | 4 | Alcantara Interior Package Pdk Sport; Aluminum Pdk Selector Agate Gray; Aluminum Pdk Selector Espresso |
| pegas | suspension-steering | brakes | 1.38 | 0.286 | 4 | ACKOJA A52-31-0005 KIA Pegas Saloon (AB) pollen filter; BLUE PRINT ADBP820027 KIA Pegas Saloon (AB) wheel bearing; BLUE PRINT ADBP820095 KIA Pegas Saloon (AB) wheel hub |
| pfa | suspension-steering | fasteners | 14.5 | 0.829 | 4 | Powerflex PFA100-10 Camber bolt; Powerflex PFA100-12 Camber bolt; Powerflex PFA100-14 Camber bolt |
| postcard | merchandise | air-conditioning-heating | 5.2 | 0.591 | 4 | Beautiful Parts for Beautiful Cars Postcard - Landscape; Beautiful Parts for Beautiful Cars Postcard - Portrait; Brilliant Brightwork Postcard |
| powerflex | suspension-steering | engine | 1.17 | 0.224 | 4 | 900 79′-93′ Powerflex Heritage Alternator adjuster arm bush ; 900 79′-93′ Powerflex Heritage Alternator mounting bracket b; 900 79′-93′ Powerflex Heritage Front Damper eye bush (2) |
| pressu | cooling | fuel-air | 1.05 | 0.188 | 4 | 1.980.011 EPS Sender Unit, oil pressure; 1PCS Brake Assist Pressure Sensor OEM 0265005329 For Nissan ; ABAKUS 232-01-033 Shock absorber Top eye, Bottom eye, Front  |
| prin | electrical | suspension-steering | 8.11 | 0.62 | 4 | 9-3SS 03′-12′ CV – Lowered Sports Chassis Aero Spring Kit (4; 9-5 Saloon 1998 to 2010 Eibach Pro-Kit Springs (set of four); ABAKUS 014-013-0001 Mercedes Sprinter 2t Minibus fan clutch |
| qoc | service-general | filters | 2.29 | 0.64 | 4 | I ELLIUQOC חובק הגה תחתון; IMED קיטסלפ IUQOC חובק הגה; QUINTON HAZELL QOC1001 Oil sump plug Steel, Thread Size: M14 |
| radi | electrical | cooling | 5.84 | 0.757 | 4 | 9-3 98′-03′ all 4 cyl Petrol models – Top Radiator to Thermo; 9-3SS 03′-11′ 4 cyl petrol turbo models (B207) – Lower Radia; 9-5 02′-10′ 4 Cylinder Petrol Auto all models – Radiator |
| refer | service-general | air-conditioning-heating | 5.15 | 0.671 | 4 | 2017 French 911 Turbo Quick Reference Gu; 911 Turbo Quick Reference Guide Us My17; Daewoo Lanos - Preferred Components Inc TS26335 |
| rezzo | suspension-steering | brakes | 1.04 | 0.288 | 4 | 18042267 abs DAEWOO REZZO SE 2005 1340136; 1810251 BRAKE LIGHT SWITCH STOP EPS FOR DAEWOO KALOS,NUBIRA,; 1x Brake Caliper Front Left for DAEWOO EVANDA, LEGANZA, CHEV |
| samsung | audio-electronics | air-conditioning-heating | 2.75 | 0.458 | 4 | Heritage E Type Samsung Galaxy Phone Cover; Leather Jaguar Samsung Galaxy Tablet Slip Case - Black; Leather Jaguar Samsung Galaxy Tablet Slip Case - Light Blue |
| satell | electrical | audio-electronics | 25.88 | 0.892 | 4 | antenna assy satell; Antenna Assy Satell; Antenna Assy Satellite |
| screwdriver | service-general | fasteners | 1.02 | 0.329 | 4 | 3pc Screwdriver; 5pc Screwdriver; 6 In 1 Quickchange Screwdriver Display |
| simulator | audio-electronics | electrical | 1.4 | 0.412 | 4 | Doppler Simulator Set Loa; Land Rover Sound Simulator; Pedal Simulator |
| sky | body-exterior | brakes | 1.67 | 0.217 | 4 | 555 SR-N760 NISSAN Skyline Saloon (V36) tie rod; A.B.S. 16040 Nissan Skyline R34 brake discs; A.B.S. 17632 NISSAN Skyline Coupe (V35) brake discs |
| slee | merchandise | brakes | 3.06 | 0.468 | 4 | ABAKUS 120-09-020 Repair Sleeve, transmission output shaft (; A.B.S. 55045 Guide sleeve kit, brake caliper Rear Axle, Left; A.B.S. 55117 Guide sleeve kit, brake caliper Front Axle, Rig |
| sli | suspension-steering | body-exterior | 2.65 | 0.535 | 4 | ABAKUS 132-042-063 Door Handle Sliding Door; ABAKUS 137-02-004 Roller Guide, sliding door Right, Lower; ABAKUS 137-02-007 Roller Guide, sliding door Left, Lower |
| snart | merchandise | clutch-drivetrain | 3.17 | 0.594 | 4 | D SE SNART ציריה מחודש יצרן; D SE SNART ציריה קדמית מחודש יצרן; E AG SNART ציריה קדמית מחודש יצרן |
| speeds | service-general | brakes | 1.79 | 0.224 | 4 | ABAKUS 141-03-017 OPEL Speedster (E01) wheel bearing; A.B.S. 200001 PORSCHE Taycan Sport Turismo wheel bearing; A.B.S. 35422 Porsche 911 964 Convertible brake pads |
| sportwage | service-general | air-conditioning-heating | 1.07 | 0.475 | 4 | Betriebsanleitung Cdr 30 Sportwagen; Betriebsanleitung Pcm3 0 Sportwagen; Betriebsanleitung Pcm Ii Sportwagen |
| stk | merchandise | air-conditioning-heating | 1.29 | 0.31 | 4 | Classic Postkarte Key Pouches De Old; Classic Postkarte Key Pouches En Old; Cstk Sems Black 8 |
| suppl | accessories | fuel-air | 2.75 | 0.318 | 4 | 3RG 82002 Renault Clio 2 Van fuel pump; Airbag-suppl; Air Conditioning Vacuum Supply Harness |
| svc | service-general | air-conditioning-heating | 2.67 | 0.471 | 4 | Chry Svc O Ring; Chy Rv2 Svc Valve Ga; Cover Svc Hole |
| tablet | accessories | air-conditioning-heating | 1.2 | 0.353 | 4 | Click And Play Tablet; Holder-Tablet; iPad 5th / 6th Gen 9.7&quot; Adaptor for Click and Go System |
| tarpaulin | accessories | interior-comfort | 1.2 | 0.5 | 4 | CAR COVER-TARPAULIN; Fastener Tarpaulin; Latch S A Tarpaulin |
| temporary | service-general | wheels-bearings | 40.0 | 0.976 | 4 | Label Temporary Tire; temporary kit w o tire; tire temporary |
| tork | service-general | fluids | 1.44 | 0.419 | 4 | CHEVROLET Suburban V10 Closed Off-Road Vehicle Diesel exhaus; FORD F4000 Mk1 Platform / Chassis Diesel exhaust fluid MOTOR; MG V80 Van Diesel exhaust fluid MOTORKIT MOT3552 |
| touchu | body-exterior | air-conditioning-heating | 2.25 | 0.643 | 4 | Paint-touchu; Paint Touchup 28g 26b; Paint Touchup A2x A4r |
| tweeter | interior-comfort | electrical | 4.31 | 0.679 | 4 | Adapter Lead Tweeter 611016; Adapter Line Tweeter 611015; Bowers & Wilkins Tweeter Speaker LH |
| types | service-general | air-conditioning-heating | 3.6 | 0.679 | 4 | Cover Rear Seat Cushion Trim Types Leather; Cover R Rear Seat Back Trim Types Leather; Front Brake Pads for Land Rover Freelander 2 - Fits All Year |
| vika | suspension-steering | engine | 1.21 | 0.168 | 4 | AUDI e-tron Sportback (GEA) body VIKA 88271809901; SKODA 130 Saloon steering VIKA 45090036901; Skoda Favorit Forman Tailgate strut VIKA 87400157101 |
| visa | suspension-steering | body-exterior | 2.9 | 0.432 | 4 | 3RG 40231 CITROËN Visa Convertible engine mount; 3RG 40260 CITROËN Visa Convertible engine mount; A.B.S. 200572 CITROËN Visa Convertible wheel bearing |
| walnut | interior-comfort | body-exterior | 1.96 | 0.363 | 4 | 4 Spoke Walnut Wheel; Center Console Trim Walnut 511608; Cover American Walnut |
| washers | service-general | cooling | 1.13 | 0.184 | 4 | 1 Set Washers; Audi A4 Convertible Brake hose RIDEX 83B0019; BLUE PRINT ADT38588 TOYOTA Venza II (XU80) anti roll bar lin |
| westfalia | suspension-steering | body-exterior | 1.34 | 0.267 | 4 | A.B.S. 200055 Volkswagen California Westfalia T4 Minibus (7D; A.B.S. 210713 Volkswagen California Westfalia T4 Minibus (7D; A.B.S. 210714 Volkswagen California Westfalia T4 Minibus (7D |
| you | merchandise | clutch-drivetrain | 1.62 | 0.317 | 4 | Bmw Youth Cap Red Navy 809016; CAP, YOUTH 68 HIBBERT FLAT BRIM; Caution Watch Your S |
| חיצון | fasteners | electrical | 1.0 | 0.308 | 4 | INSIDE THUMB TURN ASSY; גומי גשם אח.שמ.חיצון; חיישן טמפרטורה חיצון |
| טפט | interior-comfort | body-exterior | 4.74 | 0.717 | 4 | Adhesive tapor; "COVER B PLR AY F RH; Leapmotor Part 000000000052205618 |
| יתר | gearbox | body-exterior | 1.78 | 0.271 | 4 | HYUNDAI 85938BE000; SsangYong Korando הילוך יתר תי'ה; אום 02M ניילוק למתיחת רשת מיתרים |
| כמות | fuel-air | electrical | 5.48 | 0.719 | 4 | Hot-film airode; HYUNDAI 393502F610; Mass Air Flow Sensor |
| להתקנת | service-general | engine | 7.0 | 0.593 | 4 | 8 ערכה להתקנת מגדש טורבו; Installation Kit Exhaust Tur 119810; RENAULT RE7711382333 |
| למיני | accessories | fuel-air | 10.0 | 0.833 | 4 | C סטרטר למיני מעמיס 034/024; O רינג למיני באגר 73XC מנוע יאנמאר MEO; ג' נט משומש 57.9X5.61 למיני מעמיס 061RS |
| לעמ | gearbox | body-exterior | 1.83 | 0.418 | 4 | 0GS תושבת לעמוד אח' את"ן; Body A Pillar Trim Panel; BOLT |
| לפיוז | body-exterior | electrical | 1.22 | 0.524 | 4 | DIODE ASSY; High voltageode; IX3 כיסוי לפיוז בטחון |
| לפלטת | electrical | fasteners | 1.0 | 0.286 | 4 | אטם לפלטת בלוק מנוע APK310-3; בורג לפלטת GTM; בורג לפלטת ביניים תמסורת אוטומטית* |
| מדיד | interior-comfort | engine | 2.38 | 0.611 | 4 | # 18 F150 מדיד שמן מנוע; AT צינור מדיד שמן גיר; Dipstick ode |
| מזוודה | accessories | interior-comfort | 1.21 | 0.333 | 4 | 20" מזוודה טרולי; * 28L R1200ST 'מזוודה אח; Case bottom, |
| מפריד | filters | fluids | 1.8 | 0.411 | 4 | Engine Crankcase Vent Valve; FORD 2778496 Oil separator, crankcase breather; Gas-liquid separator |
| משקל | accessories | electrical | 11.75 | 0.77 | 4 | LR/JAG Part - בולם קדמי BIG BORE למביני עניין מתוצרת TERRAFI; LR/JAG Part - שאקל סינטטי בעובי 10 מ"מ קל משקל ויותר נוח לתפ; OEM Part - חיישן משקל מושב נוסע |
| עזרה | safety-systems | electrical | 1.0 | 0.286 | 4 | First Aid Box; First-aid ki; KIT-FIRST AID |
| פטנט | service-general | fasteners | 36.0 | 0.973 | 4 | HYUNDAI 1243303107B; HYUNDAI 1249204147B; HYUNDAI 88091AA100 |
| פנורמי | body-exterior | lighting | 1.31 | 0.463 | 4 | GLASS; Headlining pode; HYUNDAI 81600BE000YPK |
| קיפול | body-exterior | interior-comfort | 2.31 | 0.566 | 4 | ACTUATOR ASY; BEZEL; Drive Backrest Folding Left 521061 |
| קרבון | accessories | engine | 1.14 | 0.353 | 4 | 218I(ספויילר אח' (קרבון; 3XL 7 קסדה איוו קרבון; 52/53 קסדת סיסטם 7 קרבון |
| ראשונה | safety-systems | electrical | 1.25 | 0.294 | 4 | First Aid Box; First-aid ki; KIT-FIRST AID |
| שטיפת | wipers-washers | lighting | 3.0 | 0.462 | 4 | 1L חומר שטיפת מערכות מיזוג BELNET; 5L חומר שטיפת מערכות מיזוג BELNET; SUZUKI P.38832.58U00.M |
| aae | service-general | air-conditioning-heating | 1.2 | 0.429 | 3 | S/S to Ghc4602daaee; S/S to Ghc4602daaem; S/S to Ghc9510aaaef |
| adc | suspension-steering | brakes | 1.08 | 0.164 | 3 | 2 x Comline Vented Front Brake Discs 256mm - ADC1052V fits D; 2X FRONT AXLE BRAKE DISCS FOR DAEWOO NEXIA OPEL ASTRA F CORS; ABAKUS 214-1938R-2ADCR Rear light Right, with bulb |
| ambition | accessories | clutch-drivetrain | 4.0 | 0.75 | 3 | Ambition Travel Mug By; Ambition Trucker Cap; Drive Your Ambition Rag |
| anch | fasteners | body-exterior | 1.88 | 0.376 | 3 | 9-3 98′-03′ Inner Control Arm (Stanchion) RH; Adapter Anchor; Anchor |
| apv | suspension-steering | belts-chains | 6.36 | 0.638 | 3 | ABAKUS 037-025-0017 OPEL Sintra (APV) engine thermostat; A.B.S. 201651 OPEL Sintra (APV) wheel bearing; A.B.S. 220511 OPEL Sintra (APV) suspension ball joint |
| ariya | suspension-steering | brakes | 1.0 | 0.333 | 3 | ALKAR 6403164 NISSAN Ariya (FE0) wing mirror; BILSTEIN 22-342638 NISSAN Ariya (FE0) shock absorber; BLUE PRINT ADBP420227 NISSAN Ariya (FE0) brake pads |
| assortme | accessories | fuel-air | 1.14 | 0.348 | 3 | ALCA 508000 Assortment, hose clamps; AMiO 02548 Assortment, hose clamps; AMiO 04125 Assortment, hose clamps |
| asymm | wheels-bearings | air-conditioning-heating | 3.4 | 0.739 | 3 | Eagle F1 Asymmetric; Eagle F1 Asymmetric 2 N; Eagle F1 Asymmetric 3 R |
| austral | suspension-steering | brakes | 1.17 | 0.19 | 3 | 12 Van Fog Light TYC 19-0096-05-2; 12 Van Rear light DIEDERICHS 1887091; 12 Van Side indicator TYC 325-0151-3 |
| available | service-general | body-exterior | 1.09 | 0.197 | 3 | 200TDI Valve and Push Rod Kit - Land Rover Defender, Range R; ABS Rear Speed Sensor for Freelander 2 - Fits Right or Left ; Air Con Compressor O Ring Kit for Freelander 2 and Range Rov |
| bolts | service-general | brakes | 3.24 | 0.525 | 3 | ABAKUS 017-025-0010 Ford Mondeo Mk5 Estate engine thermostat; ABAKUS 123-00-033 Rocker cover with gaskets/seals, with bolt; ABAKUS 123-00-113 Rocker cover with gasket/seal, with bolts/ |
| bott | fluids | suspension-steering | 7.84 | 0.725 | 3 | ABAKUS 232-01-033 Shock absorber Top eye, Bottom eye, Front ; ABAKUS 232-02-008 Shock absorber Top eye, Bottom eye, Rear A; ABAKUS 232-02-012 Shock absorber Top eye, Bottom eye, Rear A |
| brx | service-general | fuel-air | 327.0 | 0.994 | 3 | BR Turbo BRX05996 Mazda 5 cw turbocharger; BR Turbo BRX05996 VOLVO V40 Cross Country (526) turbocharger; BR Turbo BRX06002 Mercedes GLE W166 turbocharger |
| burglar | safety-systems | electrical | 1.12 | 0.3 | 3 | Adapter Lead Radio Burglar A C611015; Brkt Burglar Alarm; Burglar |
| buzz | suspension-steering | electrical | 3.15 | 0.412 | 3 | A.B.S. 18999 Volkswagen ID. Buzz Minibus brake discs; A.B.S. 35337 Volkswagen ID. Buzz Minibus brake pads; AISIN BPVG-7030 Volkswagen ID. Buzz Minibus brake pads |
| caged | service-general | fasteners | 2.4 | 0.632 | 3 | Caged Nut; Caged Weld and Tapped Nut; Caged Weld Nut |
| cardigan | merchandise | air-conditioning-heating | 5.33 | 0.842 | 3 | Cardigan; Long Knit Cardigan; Women's Knit Cardigan |
| cashmer | merchandise | interior-comfort | 3.62 | 0.607 | 3 | Adhesive Carpet Cashmere Beige; Airbag Unit Cashmere Beige; armrest assembly console yr449l cashmere ivory |
| certificate | service-general | air-conditioning-heating | 6.45 | 0.855 | 3 | Certificate Template Certified Porsche; Certificate Template Zertifizierter Por; Ec Certificate Of Conformity 861001 |
| coffee | merchandise | air-conditioning-heating | 2.31 | 0.357 | 3 | Airtight Coffee Tin Black Brown; Antenna Assembly Radio And Telematics And Gps And Xm Yr600m ; Antenna Assembly Radio And Xm Yr600m Kona Coffee Metallic |
| communication | audio-electronics | air-conditioning-heating | 1.33 | 0.51 | 3 | Adapter For Vehicle Communication; Adapter For Vehicle Communication China; Adapter For Vehicle Communication Diagno |
| contisportcontac | wheels-bearings | air-conditioning-heating | 3.91 | 0.754 | 3 | Continental Contisportcontac 369041; Contisportcontact; Contisportcontact 2 Ao |
| coole | service-general | cooling | 3.92 | 0.537 | 3 | 3RG 81249 Engine oil cooler; 9000 91′-98′ (see descr) – DO88 Intercooler Hose Kit; 9-3 00′-03′ Manual Petrol – DO88 Intercooler Pressure Pipe & |
| cummins | engine | fluids | 6.79 | 0.625 | 3 | Aluminium Conversion Radiator for Defender with Cummins 2.8 ; Boost Tube Upgrade Kit 2007-2009 Ram 6.7L Cummins Banks Powe; Boost Tube Upgrade Kit 2013-2018 Ram 6.7L Cummins Banks Powe |
| cwp | gearbox | cooling | 1.32 | 0.53 | 3 | Audi 100 Coupe Windscreen washer pump CASCO CWP74000AS; AUDI A3 Saloon (8YS) Windscreen washer pump CASCO CWP75002AS; AUDI R8 Spyder (427, 429) Windscreen washer pump CASCO CWP75 |
| dampe | service-general | suspension-steering | 12.81 | 0.715 | 3 | 900 / 900 Turbo 1979 – 1993 Avo Adjustable Performance Front; ACKOJA A53-1530 KIA Sedona I (GQ) shock absorber; AUTOMEGA 160064611 Vibration Damper, v-ribbed belt |
| dams | body-exterior | wheels-bearings | 1.47 | 0.434 | 3 | 2019-2023 Dodge Ram 2500 - 3500 Diesel 5th Generation Front ; 2019-2023 Dodge Ram 2500 - 3500 Diesel 5th Generation Front ; Adams Driveshaft CJ Front Slip N Stub 1310 Driveshaft Extrem |
| derringer | audio-electronics | fuel-air | 1.17 | 0.333 | 3 | Derringer Tuner (Gen2) with iDash 1.8 DataMonster 2017-19 Ch; Derringer Tuner w/DataMonster includes ActiveSafety and Bank; Derringer Tuner with ActiveSafety and Banks iDash Super Gaug |
| divers | hybrid-ev | audio-electronics | 8.0 | 0.649 | 3 | Adapter Cable Diversity 611016; Antenna Amplifier Diversity 652041; Antenna Amplifier Diversity C651022 |
| drs | service-general | brakes | 1.26 | 0.275 | 3 | 2018 DACIA DUSTER PRESTIGE H79 1.2 TCE MANUAL MK1 5DRS HATCH; 2X FRONT BRAKE DISC RETAINING SCREWS FIT: LANDROVER RANGE RO; 2X REAR BRAKE DISC RETAINING SCREW FIT: RANGE ROVER SPORT 13 |
| dwntrn | electrical | body-exterior | 1.14 | 0.533 | 3 | Adptr-Dwntrn PP Pems; Adptr-Dwntrn PP PEMS; ADPTR-DWNTRN PP PEMS |
| esr | service-general | body-exterior | 2.14 | 0.417 | 3 | A ESREVART גשר אח ימין; A ESREVART גשר לגג; A ESREVART טרברסה |
| experience | merchandise | air-conditioning-heating | 1.4 | 0.5 | 3 | Flyer Event Packages Porsche Experience; Porsche Experience Magazine Storie Mym; Porsche Experience Stories |
| fch | service-general | air-conditioning-heating | 1.5 | 0.462 | 3 | Bracket Fchv Motor; Cable Assy Fchv Tra; DENSO VFCH16 SUBARU Trezia II (DBA-NSP120) spark plug |
| feu | fasteners | lighting | 11.0 | 0.846 | 3 | CO DRA FEU פנס אחורי; CO DRA FEU פנס אחורי ימין; CO GRA FEU פנס אחורי |
| finis | service-general | body-exterior | 80.16 | 0.95 | 3 | Alternator Assembly, Black Chrome Finish, 90 amp; A-Post Closing Finisher LH; Bonnet Air Vent Finisher, Left Hand |
| flg | service-general | fasteners | 2.67 | 0.436 | 3 | 05 15 Frn 5 Bkflg2; 05 15 Frn 6 Bkflg2; BOLT BOLT FLG 12X82.5X |
| gga | service-general | body-exterior | 1.55 | 0.377 | 3 | 6 Series Embroidered Luggage Compartment Mat; All Weather Luggage Compartment Mat; bar luggage door hi |
| glue | service-general | engine | 2.0 | 0.364 | 3 | Dos Super Glue; Electric Glue G; Glue Battery Case |
| gri | fluids | body-exterior | 15.84 | 0.838 | 3 | ABAKUS 037-34-454 Opel Astra H bumper grill; ABAKUS 442-2509R-UDN Bumper grill Right, with hole(s) for fo; Air Outlet Grille Left C514080 |
| hcet | accessories | interior-comfort | 2.0 | 0.615 | 3 | TIK MIV I HCET; גגון 2 מוטות HCET-E; כיסוי רכב enageM hcet-E |
| hexagona | service-general | fasteners | 2.67 | 0.627 | 3 | Hexagonal Compressor Nut; Hexagonal Flange Nut; Hexagonal Head Bolt |
| inc | service-general | suspension-steering | 1.62 | 0.245 | 3 | 10inch Powered Sub-Woofer - 5-Door - W/O Navigation; 1 2 Inch Plastic Tape 759012; 19 Inch 6 Spoke Black Diamond Cut |
| injectio | engine | fuel-air | 2.69 | 0.53 | 3 | 3RG 81291 Heat Shield, injection system; 3RG 81312 Heat Shield, injection system; 3RG 81313 Heat Shield, injection system |
| istana | service-general | engine | 1.1 | 0.231 | 3 | A.B.S. 37927 SSANGYONG Istana Van brake pads; A.B.S. 8125 SSANGYONG Nomad SUV drum brake; AISIN JTRDO-014 SSANGYONG Istana Van track rod end |
| jacket | accessories | air-conditioning-heating | 1.02 | 0.462 | 3 | 25y Carrera Gt Jacket Black Beige; 25y Carrera Gt Jacket Silver; 3 Layer Jacket |
| jounce | suspension-steering | body-exterior | 3.65 | 0.753 | 3 | "BUMPER, Jounce"; BUMPER JOUNCE; "BUMPER, Jounce, [X91]" |
| keyri | accessories | body-exterior | 2.75 | 0.55 | 3 | 2 Tier Keyring pack; 3 Tier Keyri; 3 Tier Keyring Pack |
| kychn | interior-comfort | air-conditioning-heating | 2.0 | 0.5 | 3 | Baron Lfi Rectangle Green Acrylic Kychn; Baron Lfi Tear Drop Kychn W Blk Insert; Baron Lfi Trillium Rounded Circle Kychn |
| maxol | fluids | interior-comfort | 1.8 | 0.529 | 3 | בקבוק קרם הגנה סיליקוני 0.5K MAXOL-MAX; פח 15L משחת ידיים MAXOL-MAX; תרסיס ניקוי חלודה MAXOL |
| meas | electrical | wipers-washers | 7.52 | 0.681 | 3 | Acc Measuring Unit Vw Vas 6430 2; Adapter For Meas Techn Dso Vas 5260a; Adapter For Meas Techn Dso Vas 5578 |
| medi | audio-electronics | interior-comfort | 1.3 | 0.217 | 3 | 192996 133206 left rear brake disc MEDIDA 5 X 105 310MM MASE; AIC 55726 Intermediate Shaft Front Axle, Right, for manual t; AJUSA 15004400 Shaft Seal, intermediate shaft |
| midlayer | merchandise | air-conditioning-heating | 2.75 | 0.733 | 3 | Golf Midlayer For Women Sand Beige; Ski Midlayer Black Grey Yellow; Ski Midlayer Caviar |
| mtm | service-general | hybrid-ev | 1.4 | 0.318 | 3 | ANTENNA ASSY-COIL MTM; Bracket Mtm Rr; Brkt Mtm Battery B |
| nitrogen | service-general | air-conditioning-heating | 31.05 | 0.96 | 3 | AVA COOLING SYSTEMS FDA5427D Air conditioning condenser with; AVA COOLING SYSTEMS FT5347D Air conditioning condenser with ; AVA COOLING SYSTEMS FTA5314D Air conditioning condenser with |
| nxn | suspension-steering | filters | 2.04 | 0.28 | 3 | ASHIKA SA-X50C FORD Transit Custom V710 Minibus (NXN) windsh; ASHIKA SA-X65C FORD Transit Custom V710 Minibus (NXN) windsh; ASHIKA SA-X65S FORD Transit Custom V710 Minibus (NXN) windsh |
| omega | suspension-steering | engine | 1.63 | 0.207 | 3 | 555 SB-T432L Opel Senator A suspension ball joint; ABAKUS 120-05-064 Opel Omega A Saloon camshaft sensor; A.B.S. 17147 Opel Omega B Saloon brake discs |
| pems | electrical | body-exterior | 1.14 | 0.533 | 3 | Adptr-Dwntrn PP Pems; Adptr-Dwntrn PP PEMS; ADPTR-DWNTRN PP PEMS |
| phase | electrical | engine | 1.52 | 0.479 | 3 | Brkt Phase Sensor; CAMSHAFT/PHASER LOCK; Camshaft phase sensor control valve |
| phl | fasteners | wheels-bearings | 1.33 | 0.235 | 3 | 10 24x1 2 Phl Pan He; 10 24x1 Phl Oval Hea; 10 24x3 4 Phl Oval H |
| potentia | fluids | engine | 1.88 | 0.616 | 3 | AJUSA 81020800 KIA Potentia Saloon (HC12) cylinder head bolt; AJUSA 81042500 KIA Potentia Saloon (HC12) cylinder head bolt; AJUSA 81045800 KIA Potentia Saloon (HC12) cylinder head bolt |
| powertrain | clutch-drivetrain | electrical | 6.26 | 0.78 | 3 | control module powertrain rewritable; Epowertrain; Module Powertrain Co |
| pyl | suspension-steering | cooling | 5.47 | 0.648 | 3 | A.B.S. 231205 SKODA Elroq (PYL) track rod end; A.B.S. 261198 SKODA Elroq (PYL) anti roll bar links; ACKOJA A52-70-0032 Ignition lead set EPDM (ethylene propylen |
| ras | service-general | body-exterior | 1.79 | 0.396 | 3 | ABAKUS 120-01-022 Parking sensor Ultrasonic Sensor, Front; ABAKUS 120-01-030 Parking sensor black, grey, Ultrasonic Sen; ABAKUS 120-01-044 Parking sensor black, Ultrasonic Sensor, F |
| receptacl | electrical | safety-systems | 2.34 | 0.667 | 3 | HOFFER 8193217 Coolant Flange without sensor, without Stud R; Land Rover Seat Belt Receptacle; mopar fuel filler receptacle |
| restric | gearbox | fuel-air | 2.4 | 0.308 | 3 | Engine Breather Hose Restrictor; Engine Mounting Roll Restrictor; Land Rover Restrictor |
| romix | service-general | body-exterior | 1.42 | 0.355 | 3 | AUDI F103 Variant engine cooling system ROMIX C60603; CITROËN Relay II Platform / Chassis (250) Trim clip ROMIX C1; CITROËN window regulator ROMIX C50867 |
| roomster | fuel-air | body-exterior | 1.22 | 0.159 | 3 | ABAKUS 103-01-013 Skoda Roomster 5j windshield wipers; ABAKUS 130-003-078 Skoda Roomster 5j window regulator; ABAKUS 132-048-001 Skoda Roomster 5j tailgate lock |
| sculpture | merchandise | air-conditioning-heating | 2.67 | 0.615 | 3 | 911 Sport Classic Sculpture Grey Metalli; 911 Sport Classic Sculpture Meissen Blue; Concept Car Sculpture |
| securing | service-general | electrical | 12.39 | 0.853 | 3 | 1 Set Securing Bolts; 3RG 15226 Securing Plate, ball joint; Armrest Securing Nut |
| sho | electrical | suspension-steering | 4.26 | 0.667 | 3 | ABAKUS 232-01-033 Shock absorber Top eye, Bottom eye, Front ; ABAKUS 232-01-170 FORD Transit Connect Mk3 MPV shock absorbe; ABAKUS 232-01-296 Renault Koleos 2 shock absorber |
| sleev | fasteners | brakes | 3.06 | 0.468 | 3 | ABAKUS 120-09-020 Repair Sleeve, transmission output shaft (; A.B.S. 55045 Guide sleeve kit, brake caliper Rear Axle, Left; A.B.S. 55117 Guide sleeve kit, brake caliper Front Axle, Rig |
| sonax | fluids | air-conditioning-heating | 1.8 | 0.5 | 3 | SONAX 01604410 Screenwash; SONAX 01605000 Screenwash; SONAX 02602000 Screenwash yellow |
| splines | clutch-drivetrain | brakes | 7.0 | 0.808 | 3 | Disc With External Splines; Grease Splines |
| spn | service-general | interior-comfort | 3.0 | 0.6 | 3 | 10 Fd Spnglock; 2n1 Micro Chnl Spng; 3/4IN-2IN ADJ PIN SPNR WR |
| ssel | service-general | interior-comfort | 1.83 | 0.458 | 3 | Bmw Schl Sselanh Nger Logo C 809027; Dresselhaus 4058/000/40 7x11 Hose Clamp; Dresselhaus 4181/000/40 15 Fuse |
| storing | interior-comfort | body-exterior | 3.0 | 0.75 | 3 | Cover Storing Partition 511607; Cover Storing Partition Asht 511607; Cover Storing Partition C511380 |
| styling | body-exterior | suspension-steering | 2.28 | 0.688 | 3 | 9-3 Sports 03′-07′ all models -Black Mesh and Black Surround; 9-3 Sports 03′-07′ all models – Chrome Mesh and Surround Sty; Body Styling Mesh Grille |
| sung | accessories | wipers-washers | 1.28 | 0.387 | 3 | Arran Sungla; A triangular sunglass; Barra Sungla |
| sunr | interior-comfort | body-exterior | 8.44 | 0.804 | 3 | air deflector sunroof; Bracket Sunroof; Bracket Sunroof Set |
| telesco | service-general | suspension-steering | 124.79 | 0.978 | 3 | ABAKUS 232-01-033 Shock absorber Top eye, Bottom eye, Front ; ABAKUS 232-02-005 Shock absorber Top pin, Bottom eye, Rear A; ABAKUS 232-02-008 Shock absorber Top eye, Bottom eye, Rear A |
| tma | service-general | suspension-steering | 3.55 | 0.536 | 3 | 1 Set Footmats Textile; 1 Set Footmats Textile Agave Green; 1 Set Footmats Textile Black |
| torp | fasteners | body-exterior | 3.0 | 0.45 | 3 | AC TCETORP כסוי ציר בגז; Airconditioningpipe Evaporatorpipeassembly; AP TCETORP ביטנה לכנף |
| townstar | suspension-steering | filters | 1.26 | 0.203 | 3 | 3RG 41684 NISSAN Townstar Van (XFK) engine mount; ABAKUS 141-01-290 NISSAN Townstar Van (XFK) wheel bearing; ABAKUS 232-02-126 NISSAN NV250 Van (X61) shock absorber |
| tss | suspension-steering | gearbox | 1.21 | 0.319 | 3 | AERIAL ASY; Alphard AH30 Top strut mount FEBEST TSS-GSA33F; Centerconsoletrim Zktssb |
| tutela | fluids | air-conditioning-heating | 1.24 | 0.33 | 3 | 1Lt Tutela As 8; 1LT TUTELA AS 8; 1LT שמן גיר TUTELA AS8 (דיילי אוטו מלא) |
| utili | accessories | suspension-steering | 1.36 | 0.203 | 3 | A-Pillar Mounting Kit For Utility-25 &amp; Utility-25 MAXX L; Bed Divider Sliding For Use W Utili Track; Bed Extender Fixed W O Utili Track |
| vie | electrical | body-exterior | 15.39 | 0.899 | 3 | Adapter Lead Rearview Camer 611015; Camera Assy Back Vie; Camera Assy Front View |
| vista | suspension-steering | body-exterior | 21.29 | 0.768 | 3 | ABAKUS 141-03-053 TOYOTA Vista / Camry Saloon (_V3_, _V4_) w; A.B.S. 201227 TOYOTA Vista / Camry Saloon (_V3_, _V4_) wheel; BLUE PRINT ADT32110 TOYOTA Vista / Camry Saloon (_V3_, _V4_) |
| vkds | service-general | suspension-steering | 19.61 | 0.934 | 3 | Audi A4 B9 Saloon Anti-roll bar link SKF VKDS 341026; AUDI suspension ball joint SKF VKDS 311008; Cadenza VG Anti-roll bar link SKF VKDS 945016 |
| voyage | suspension-steering | brakes | 1.63 | 0.29 | 3 | 101-1015 DRUM WHEEL BRAKE CYLINDER PAIR REAR CIFAM 2PCS FOR ; 16018401 left front brake caliper 114052 CHRYSLER GRAND VOYA; 1x Brake Pads Rear for CHRYSLER VOYAGER IV 00- |
| wifi | electrical | brakes | 1.6 | 0.5 | 3 | APPLE IPAD 2 16GB WIFI; Feeder Antenna Wifi; Leapmotor Part 000000009864985480 |
| wuzetem | service-general | fuel-air | 1.83 | 0.647 | 3 | Wuzetem PDLLA139P2229 Injector Nozzle; Wuzetem PDLLA148P924D Nozzle Body; Wuzetem PDLLA148P924 Nozzle Body |
| xde | suspension-steering | filters | 2.88 | 0.422 | 3 | ABAKUS 141-03-092 NISSAN Interstar Platform/Chassis (XDE) wh; A.B.S. 18164 NISSAN Interstar Platform/Chassis (XDE) brake d; AIC 59614 NISSAN Interstar Platform/Chassis (XDE) wheel bear |
| xfk | suspension-steering | filters | 1.26 | 0.204 | 3 | 3RG 41684 NISSAN Townstar Van (XFK) engine mount; ABAKUS 141-01-290 NISSAN Townstar Van (XFK) wheel bearing; ABAKUS 232-02-126 NISSAN NV250 Van (X61) shock absorber |
| your | merchandise | clutch-drivetrain | 3.25 | 0.481 | 3 | Caution Watch Your S; Contact Your Dpsm; Discovery 2 Diff Lock Lever Kit - Perfect Upgrade to get Dif |
| אופניים | accessories | interior-comfort | 2.61 | 0.618 | 3 | 1. מנשא אופניים לוו גרירה 3 זוגות אופניים 2G yaworuE עם בסיס; 3. מנשא אופניים לוו גרירה 3 זוגות אופניים 279 nognaH ננעל עם; Bicycle roofode |
| אתות | fasteners | lighting | 2.62 | 0.677 | 3 | EU AVANT פנס אתות קד' י; EU INDIC פנס אתות קד' י; EU INDIC פנס אתות ק' ש |
| גניבה | safety-systems | electrical | 8.67 | 0.839 | 3 | NLS מחשב נגד גניבה; OEM Part - אזעקה נגד גניבה; OEM Part חיישן בקרה נגד גניבה |
| המיטא | engine | lighting | 1.67 | 0.435 | 3 | automatic transmission filter o ring; belt molding; Bracket |
| הנהג | safety-systems | body-exterior | 1.43 | 0.256 | 3 | Driver's seat controller; SMART 8892558442; SMART 8892593201 |
| למבודד | service-general | engine | 1.4 | 0.368 | 3 | 20 תפס למבודד כנף אקס; CLIP; FASTENER,BONNET |
| לערכת | service-general | wheels-bearings | 1.75 | 0.35 | 3 | HYUNDAI 52933K0180; Screwdriver or; אום לערכת תפוחים RECALL N49 |
| מודל | merchandise | body-exterior | 1.25 | 0.385 | 3 | AERIAL ASY; Clutch Switch; Longitudinal |
| ספוגי | interior-comfort | body-exterior | 1.56 | 0.452 | 3 | OEM ספוגית מרווח לגוף קירור סוללה; Zeekr Part 8890895278; Zeekr Part 8890895292 |
| עילית | interior-comfort | electrical | 1.7 | 0.347 | 3 | C5 CX5 תצוגה עילית; CONTROLLER-HEADS UP DISPL; COVER,METER HOOD |
| פזרקס | fluids | electrical | 2.5 | 0.5 | 3 | 205LT חבית שמן גיר פזרקס EP80W; פזרקס 75W85; פזרקס 80W90 HD |
| פיקוח | electrical | wheels-bearings | 4.33 | 0.722 | 3 | GENESIS 95460L1000; GENESIS 95460T6010; HYUNDAI 95460DY100 |
| פנים | interior-comfort | lighting | 3.0 | 0.476 | 3 | Adapter leador; Dome Lamp; Front Left Wheel Arch Welcome Light |
| קטלוגי | service-general | lighting | 1.75 | 0.35 | 3 | (EPC כבל ידית הילוכים (לבדוק מספר קטלוגי לפי; EPC כבל ידית הילוכים לבדוק מספר קטלוגי לפי; EPC כבל להילוכים לבדוק מספר קטלוגי לפי |
| ריח | interior-comfort | body-exterior | 7.57 | 0.82 | 3 | FRT DR LATCH LH; G12 ריחן בתא כפפות; G22 כרית אוויריחידתבקרה |

## Ambiguous (226)

Proposed category is the top one but does not beat the runner-up by 1.8x — needs a longer phrase, not this word alone.

| token | proposed | margin | top-2 spread | obs |
|---|---|---|---|---|
| tape | air-conditioning-heating | 1.14 | air-conditioning-heating:635, body-exterior:559 | 20803 |
| short | engine | 1.59 | engine:810, electrical:511 | 16296 |
| channel | air-conditioning-heating | 1.66 | air-conditioning-heating:1728, body-exterior:1040 | 6262 |
| cleaner | air-conditioning-heating | 1.23 | air-conditioning-heating:874, filters:709 | 5529 |
| extension | belts-chains | 1.03 | belts-chains:2686, body-exterior:2597 | 5224 |
| vacuum | fuel-air | 1.2 | fuel-air:2030, brakes:1688 | 3899 |
| heat | air-conditioning-heating | 1.71 | air-conditioning-heating:8113, body-exterior:4735 | 3360 |
| כפפות | interior-comfort | 1.22 | interior-comfort:311, body-exterior:255 | 3056 |
| microphone | audio-electronics | 1.01 | audio-electronics:146, air-conditioning-heating:144 | 2516 |
| blank | body-exterior | 1.28 | body-exterior:7352, air-conditioning-heating:5746 | 2143 |
| bungs | fluids | 1.21 | fluids:17, fasteners:14 | 2110 |
| atm | hybrid-ev | 1.38 | hybrid-ev:36, electrical:26 | 1864 |
| connection | electrical | 1.37 | electrical:596, cooling:436 | 1018 |
| crw | air-conditioning-heating | 1.4 | air-conditioning-heating:7, merchandise:5 | 999 |
| luggage | body-exterior | 1.53 | body-exterior:1335, electrical:875 | 894 |
| fob | electrical | 1.27 | electrical:195, exhaust:154 | 880 |
| פניון | electrical | 1.26 | electrical:29, wheels-bearings:23 | 826 |
| הנע | clutch-drivetrain | 1.22 | clutch-drivetrain:158, wheels-bearings:129 | 678 |
| cpt | gearbox | 1.29 | gearbox:49, air-conditioning-heating:38 | 631 |
| expansi | cooling | 1.19 | cooling:3491, air-conditioning-heating:2945 | 620 |
| access | body-exterior | 1.09 | body-exterior:1822, brakes:1673 | 456 |
| discontinued | brakes | 1.02 | brakes:51, fuel-air:50 | 407 |
| לצירייה | clutch-drivetrain | 1.07 | clutch-drivetrain:31, wheels-bearings:29 | 333 |
| לעמוד | body-exterior | 1.8 | body-exterior:295, suspension-steering:164 | 316 |
| sec | wheels-bearings | 1.17 | wheels-bearings:1405, lighting:1196 | 311 |
| canister | fuel-air | 1.64 | fuel-air:985, air-conditioning-heating:599 | 247 |
| cinturato | air-conditioning-heating | 1.77 | air-conditioning-heating:69, wheels-bearings:39 | 227 |
| axel | clutch-drivetrain | 1.38 | clutch-drivetrain:11, filters:8 | 200 |
| לפלסטיק | body-exterior | 1.06 | body-exterior:87, engine:82 | 175 |
| expansion | cooling | 1.18 | cooling:3477, air-conditioning-heating:2944 | 170 |
| petrol | fuel-air | 1.3 | fuel-air:360, engine:276 | 141 |
| constant | belts-chains | 1.0 | belts-chains:18, air-conditioning-heating:18 | 133 |
| jic | wheels-bearings | 1.05 | wheels-bearings:60, cooling:57 | 131 |
| frequency | air-conditioning-heating | 1.07 | air-conditioning-heating:15, audio-electronics:14 | 97 |
| platform | suspension-steering | 1.71 | suspension-steering:3278, filters:1912 | 96 |
| יציאת | fuel-air | 1.39 | fuel-air:202, cooling:145 | 95 |
| bypass | cooling | 1.58 | cooling:348, fuel-air:220 | 92 |
| nty | suspension-steering | 1.38 | suspension-steering:4198, electrical:3037 | 91 |
| eibach | suspension-steering | 1.22 | suspension-steering:855, wheels-bearings:703 | 84 |
| נגן | brakes | 1.5 | brakes:6, lighting:4 | 81 |
| חשמלית | electrical | 1.23 | electrical:184, cooling:150 | 76 |
| plenum | fuel-air | 1.02 | fuel-air:127, body-exterior:125 | 66 |
| לנוסע | safety-systems | 1.62 | safety-systems:55, lighting:34 | 64 |
| מעבר | cooling | 1.1 | cooling:153, fuel-air:139 | 52 |
| אביזרי | belts-chains | 1.38 | belts-chains:44, wheels-bearings:32 | 49 |
| adbp | suspension-steering | 1.12 | suspension-steering:532, engine:477 | 44 |
| hazell | suspension-steering | 1.4 | suspension-steering:1646, belts-chains:1173 | 43 |
| pickup | suspension-steering | 1.36 | suspension-steering:1382, engine:1018 | 43 |
| quinton | suspension-steering | 1.4 | suspension-steering:1646, belts-chains:1173 | 43 |
| skf | suspension-steering | 1.53 | suspension-steering:2973, belts-chains:1946 | 42 |
| kamoka | suspension-steering | 1.0 | suspension-steering:3065, engine:3054 | 40 |
| mapco | suspension-steering | 1.59 | suspension-steering:2591, brakes:1631 | 40 |
| grand | suspension-steering | 1.63 | suspension-steering:1570, brakes:964 | 39 |
| מפצל | cooling | 1.05 | cooling:23, fuel-air:22 | 38 |
| diederichs | body-exterior | 1.33 | body-exterior:4988, lighting:3764 | 37 |
| rug | interior-comfort | 1.09 | interior-comfort:85, body-exterior:78 | 36 |
| mpv | suspension-steering | 1.75 | suspension-steering:1154, engine:659 | 35 |
| wezel | body-exterior | 1.73 | body-exterior:5984, lighting:3462 | 35 |
| במטען | body-exterior | 1.3 | body-exterior:13, lighting:10 | 35 |
| transit | suspension-steering | 1.6 | suspension-steering:945, engine:591 | 34 |
| slevve | hybrid-ev | 1.67 | hybrid-ev:30, interior-comfort:18 | 30 |
| accord | suspension-steering | 1.67 | suspension-steering:757, brakes:453 | 29 |
| synchromesh | gearbox | 1.31 | gearbox:72, suspension-steering:55 | 27 |
| נוזל | fluids | 1.11 | fluids:235, cooling:211 | 27 |
| connecti | engine | 1.31 | engine:1732, electrical:1322 | 26 |
| ראדיאטור | cooling | 1.02 | cooling:67, body-exterior:66 | 26 |
| fag | suspension-steering | 1.35 | suspension-steering:1335, wheels-bearings:989 | 25 |
| optimal | suspension-steering | 1.39 | suspension-steering:1313, wheels-bearings:945 | 25 |
| oyodo | suspension-steering | 1.45 | suspension-steering:1214, engine:840 | 25 |
| cruiser | suspension-steering | 1.44 | suspension-steering:607, brakes:421 | 24 |
| corsa | suspension-steering | 1.7 | suspension-steering:609, engine:358 | 20 |
| passat | suspension-steering | 1.72 | suspension-steering:543, body-exterior:315 | 20 |
| wty | fluids | 1.69 | fluids:22, interior-comfort:13 | 20 |
| toe | suspension-steering | 1.11 | suspension-steering:40, air-conditioning-heating:36 | 19 |
| viii | suspension-steering | 1.68 | suspension-steering:466, brakes:278 | 18 |
| rheostat | electrical | 1.05 | electrical:160, lighting:153 | 17 |
| elect | electrical | 1.79 | electrical:9174, lighting:5139 | 16 |
| megane | suspension-steering | 1.55 | suspension-steering:437, body-exterior:282 | 16 |
| משען | suspension-steering | 1.14 | suspension-steering:8, interior-comfort:7 | 16 |
| calliper | brakes | 1.66 | brakes:73, air-conditioning-heating:44 | 15 |
| roadster | suspension-steering | 1.59 | suspension-steering:282, filters:177 | 15 |
| nedc | merchandise | 1.6 | merchandise:16, exhaust:10 | 14 |
| sweater | merchandise | 1.17 | merchandise:14, air-conditioning-heating:12 | 14 |
| vitara | suspension-steering | 1.49 | suspension-steering:303, filters:204 | 14 |
| נחיר | cooling | 1.04 | cooling:28, wipers-washers:27 | 13 |
| רוחץ | wipers-washers | 1.68 | wipers-washers:57, body-exterior:34 | 13 |
| bluebird | suspension-steering | 1.13 | suspension-steering:140, engine:124 | 12 |
| dynamo | electrical | 1.36 | electrical:61, air-conditioning-heating:45 | 12 |
| hardtop | body-exterior | 1.47 | body-exterior:44, suspension-steering:30 | 12 |
| vivaro | suspension-steering | 1.45 | suspension-steering:207, engine:143 | 12 |
| crosser | suspension-steering | 1.63 | suspension-steering:96, filters:59 | 11 |
| espace | suspension-steering | 1.59 | suspension-steering:272, engine:171 | 11 |
| insignia | suspension-steering | 1.73 | suspension-steering:234, electrical:135 | 11 |
| interstar | suspension-steering | 1.64 | suspension-steering:240, filters:146 | 11 |
| maxima | suspension-steering | 1.57 | suspension-steering:149, brakes:95 | 11 |
| miniatur | merchandise | 1.2 | merchandise:12, lighting:10 | 11 |
| primastar | suspension-steering | 1.08 | suspension-steering:204, engine:189 | 11 |
| scudo | suspension-steering | 1.51 | suspension-steering:317, engine:210 | 11 |
| slot | fasteners | 1.37 | fasteners:97, suspension-steering:71 | 11 |
| sylphy | suspension-steering | 1.54 | suspension-steering:60, engine:39 | 11 |
| transporter | suspension-steering | 1.48 | suspension-steering:392, brakes:265 | 11 |
| twingo | suspension-steering | 1.58 | suspension-steering:210, body-exterior:133 | 11 |
| cerato | suspension-steering | 1.57 | suspension-steering:307, filters:196 | 10 |
| distributi | electrical | 1.55 | electrical:171, engine:110 | 10 |
| רתמת | electrical | 1.72 | electrical:43, interior-comfort:25 | 10 |
| aircross | suspension-steering | 1.5 | suspension-steering:255, engine:170 | 9 |
| colt | suspension-steering | 1.56 | suspension-steering:331, filters:212 | 9 |
| doblo | suspension-steering | 1.0 | suspension-steering:291, engine:290 | 9 |
| elantra | suspension-steering | 1.63 | suspension-steering:314, engine:193 | 9 |
| enterprise | suspension-steering | 1.77 | suspension-steering:106, engine:60 | 9 |
| teana | suspension-steering | 1.79 | suspension-steering:77, wheels-bearings:43 | 9 |
| מיזוג | air-conditioning-heating | 1.43 | air-conditioning-heating:124, electrical:87 | 9 |
| סוף | electrical | 1.15 | electrical:86, body-exterior:75 | 9 |
| cat | exhaust | 1.57 | exhaust:10709, lighting:6812 | 8 |
| jumper | electrical | 1.06 | electrical:230, suspension-steering:216 | 8 |
| legacy | suspension-steering | 1.3 | suspension-steering:178, filters:137 | 8 |
| movano | suspension-steering | 1.53 | suspension-steering:342, filters:223 | 8 |
| statement | merchandise | 1.2 | merchandise:12, engine:10 | 8 |
| vito | suspension-steering | 1.71 | suspension-steering:346, brakes:202 | 8 |
| auris | suspension-steering | 1.42 | suspension-steering:78, filters:55 | 7 |
| bipper | suspension-steering | 1.69 | suspension-steering:88, engine:52 | 7 |
| courier | suspension-steering | 1.44 | suspension-steering:207, engine:144 | 7 |
| frontera | suspension-steering | 1.12 | suspension-steering:127, filters:113 | 7 |
| hyd | suspension-steering | 1.78 | suspension-steering:3809, engine:2136 | 7 |
| igm | electrical | 1.2 | electrical:36, suspension-steering:30 | 7 |
| impreza | suspension-steering | 1.67 | suspension-steering:251, filters:150 | 7 |
| impulse | electrical | 1.65 | electrical:28, wheels-bearings:17 | 7 |
| outback | suspension-steering | 1.75 | suspension-steering:213, filters:122 | 7 |
| proace | suspension-steering | 1.38 | suspension-steering:279, filters:202 | 7 |
| ulysse | suspension-steering | 1.61 | suspension-steering:108, filters:67 | 7 |
| עור | interior-comfort | 1.58 | interior-comfort:95, suspension-steering:60 | 7 |
| creta | suspension-steering | 1.08 | suspension-steering:26, brakes:24 | 6 |
| crossland | suspension-steering | 1.6 | suspension-steering:64, engine:40 | 6 |
| koup | suspension-steering | 1.26 | suspension-steering:73, engine:58 | 6 |
| pinin | suspension-steering | 1.52 | suspension-steering:47, filters:31 | 6 |
| sirius | audio-electronics | 1.64 | audio-electronics:23, lighting:14 | 6 |
| syntheti | fluids | 1.46 | fluids:805, gearbox:552 | 6 |
| tigra | suspension-steering | 1.53 | suspension-steering:89, brakes:58 | 6 |
| touareg | suspension-steering | 1.64 | suspension-steering:176, engine:107 | 6 |
| עמדת | hybrid-ev | 1.5 | hybrid-ev:6, electrical:4 | 6 |
| agila | suspension-steering | 1.43 | suspension-steering:113, filters:79 | 5 |
| alero | suspension-steering | 1.28 | suspension-steering:41, engine:32 | 5 |
| bomb | merchandise | 1.29 | merchandise:9, air-conditioning-heating:7 | 5 |
| evanda | suspension-steering | 1.2 | suspension-steering:104, brakes:87 | 5 |
| forester | suspension-steering | 1.56 | suspension-steering:159, electrical:102 | 5 |
| fullback | suspension-steering | 1.78 | suspension-steering:64, brakes:36 | 5 |
| galant | suspension-steering | 1.21 | suspension-steering:143, filters:118 | 5 |
| gle | suspension-steering | 1.64 | suspension-steering:798, body-exterior:486 | 5 |
| gmt | suspension-steering | 1.77 | suspension-steering:55, engine:31 | 5 |
| grandis | suspension-steering | 1.34 | suspension-steering:79, engine:59 | 5 |
| haynes | merchandise | 1.5 | merchandise:6, body-exterior:4 | 5 |
| koleos | suspension-steering | 1.59 | suspension-steering:126, brakes:79 | 5 |
| lubrica | fluids | 1.78 | fluids:66, wheels-bearings:37 | 5 |
| maili | merchandise | 1.03 | merchandise:37, air-conditioning-heating:36 | 5 |
| orifice | fuel-air | 1.06 | fuel-air:19, engine:18 | 5 |
| regata | suspension-steering | 1.5 | suspension-steering:39, electrical:26 | 5 |
| strada | suspension-steering | 1.3 | suspension-steering:61, filters:47 | 5 |
| suskit | suspension-steering | 1.33 | suspension-steering:8, cooling:6 | 5 |
| xii | suspension-steering | 1.61 | suspension-steering:74, brakes:46 | 5 |
| שלישי | gearbox | 1.79 | gearbox:34, interior-comfort:19 | 5 |
| aircon | air-conditioning-heating | 1.66 | air-conditioning-heating:58, electrical:35 | 4 |
| alltrack | suspension-steering | 1.18 | suspension-steering:78, electrical:66 | 4 |
| bol | fasteners | 1.51 | fasteners:14649, engine:9682 | 4 |
| bravo | suspension-steering | 1.62 | suspension-steering:118, body-exterior:73 | 4 |
| bwy | suspension-steering | 1.37 | suspension-steering:37, filters:27 | 4 |
| cirrus | suspension-steering | 1.33 | suspension-steering:40, brakes:30 | 4 |
| citycarver | suspension-steering | 1.14 | suspension-steering:33, electrical:29 | 4 |
| evasion | suspension-steering | 1.53 | suspension-steering:55, brakes:36 | 4 |
| footwell | interior-comfort | 1.47 | interior-comfort:107, brakes:73 | 4 |
| gbh | suspension-steering | 1.14 | suspension-steering:33, electrical:29 | 4 |
| gentra | suspension-steering | 1.31 | suspension-steering:59, filters:45 | 4 |
| kizashi | suspension-steering | 1.1 | suspension-steering:22, engine:20 | 4 |
| klaj | suspension-steering | 1.75 | suspension-steering:14, filters:8 | 4 |
| kletn | suspension-steering | 1.46 | suspension-steering:41, filters:28 | 4 |
| lft | body-exterior | 1.23 | body-exterior:16, belts-chains:13 | 4 |
| monterey | suspension-steering | 1.3 | suspension-steering:61, filters:47 | 4 |
| pde | suspension-steering | 1.37 | suspension-steering:48, electrical:35 | 4 |
| rcz | suspension-steering | 1.16 | suspension-steering:44, engine:38 | 4 |
| seicento | suspension-steering | 1.19 | suspension-steering:69, electrical:58 | 4 |
| sigma | suspension-steering | 1.17 | suspension-steering:27, cooling:23 | 4 |
| spacetourer | suspension-steering | 1.63 | suspension-steering:132, engine:81 | 4 |
| sweate | merchandise | 1.5 | merchandise:18, air-conditioning-heating:12 | 4 |
| sylvania | lighting | 1.75 | lighting:7, air-conditioning-heating:4 | 4 |
| tiguan | suspension-steering | 1.42 | suspension-steering:149, engine:105 | 4 |
| tot | fluids | 1.42 | fluids:555, suspension-steering:392 | 4 |
| trnsvrs | suspension-steering | 1.67 | suspension-steering:5, interior-comfort:3 | 4 |
| vdpd | suspension-steering | 1.14 | suspension-steering:33, filters:29 | 4 |
| דיאפרגמה | fuel-air | 1.0 | fuel-air:6, interior-comfort:6 | 4 |
| לסופג | suspension-steering | 1.38 | suspension-steering:11, body-exterior:8 | 4 |
| לפלסטי | body-exterior | 1.05 | body-exterior:87, engine:83 | 4 |
| מניע | clutch-drivetrain | 1.28 | clutch-drivetrain:32, engine:25 | 4 |
| עיטור | body-exterior | 1.4 | body-exterior:7, exhaust:5 | 4 |
| שים | body-exterior | 1.63 | body-exterior:206, merchandise:126 | 4 |
| airdam | body-exterior | 1.75 | body-exterior:7, air-conditioning-heating:4 | 3 |
| ampera | suspension-steering | 1.42 | suspension-steering:47, engine:33 | 3 |
| beetle | suspension-steering | 1.45 | suspension-steering:129, body-exterior:89 | 3 |
| celerio | suspension-steering | 1.4 | suspension-steering:63, filters:45 | 3 |
| citigo | suspension-steering | 1.31 | suspension-steering:102, body-exterior:78 | 3 |
| commodore | suspension-steering | 1.33 | suspension-steering:69, filters:52 | 3 |
| elroq | suspension-steering | 1.48 | suspension-steering:62, brakes:42 | 3 |
| epica | suspension-steering | 1.09 | suspension-steering:82, brakes:75 | 3 |
| fastback | suspension-steering | 1.67 | suspension-steering:55, cooling:33 | 3 |
| five | suspension-steering | 1.25 | suspension-steering:84, engine:67 | 3 |
| gfc | suspension-steering | 1.64 | suspension-steering:23, cooling:14 | 3 |
| ggr | suspension-steering | 1.22 | suspension-steering:45, engine:37 | 3 |
| grandeur | suspension-steering | 1.6 | suspension-steering:160, brakes:100 | 3 |
| hiace | suspension-steering | 1.47 | suspension-steering:251, filters:171 | 3 |
| hos | cooling | 1.26 | cooling:24251, fuel-air:19261 | 3 |
| hundred | suspension-steering | 1.21 | suspension-steering:80, engine:66 | 3 |
| idea | suspension-steering | 1.02 | suspension-steering:55, electrical:54 | 3 |
| naw | suspension-steering | 1.28 | suspension-steering:46, filters:36 | 3 |
| patrol | suspension-steering | 1.58 | suspension-steering:217, filters:137 | 3 |
| pixo | suspension-steering | 1.66 | suspension-steering:83, filters:50 | 3 |
| premio | suspension-steering | 1.43 | suspension-steering:40, brakes:28 | 3 |
| primera | suspension-steering | 1.22 | suspension-steering:218, body-exterior:178 | 3 |
| rhn | suspension-steering | 1.16 | suspension-steering:51, filters:44 | 3 |
| saa | suspension-steering | 1.49 | suspension-steering:662, engine:444 | 3 |
| tayron | suspension-steering | 1.04 | suspension-steering:50, engine:48 | 3 |
| tipo | suspension-steering | 1.21 | suspension-steering:116, body-exterior:96 | 3 |
| wfvt | suspension-steering | 1.28 | suspension-steering:23, brakes:18 | 3 |
| wgr | suspension-steering | 1.55 | suspension-steering:87, engine:56 | 3 |
| לברך | cooling | 1.2 | cooling:12, engine:10 | 3 |
| לדגם | body-exterior | 1.4 | body-exterior:21, lighting:15 | 3 |
| למניפת | cooling | 1.0 | cooling:11, engine:11 | 3 |
| לסולר | fuel-air | 1.08 | fuel-air:13, electrical:12 | 3 |
| תנודות | suspension-steering | 1.06 | suspension-steering:18, engine:17 | 3 |

## Insufficient evidence (558)

Fewer than 12 already-classified parts contain the token; the gate cannot clear these. Listed: token (proposed, obs, sample).

`midweight` (air-conditioning-heating, 999, 10), `סיוק` (merchandise, 617, 5), `ilsx` (air-conditioning-heating, 538, 3), `פלנטרים` (clutch-drivetrain, 496, 10), `ftkit` (interior-comfort, 483, 10), `אקסנט` (wheels-bearings, 454, 2), `gitter` (air-conditioning-heating, 449, 3), `lufteinlass` (air-conditioning-heating, 449, 1), `mdrivjktl` (audio-electronics, 391, 0), `לגוברנר` (fasteners, 390, 11), `חילוק` (lighting, 388, 1), `רבעון` (suspension-steering, 367, 4), `solbat` (electrical, 340, 0), `qme` (hybrid-ev, 326, 10), `סימניה` (electrical, 324, 0), `אבטחות` (safety-systems, 305, 1), `wam` (electrical, 304, 2), `קושיח` (merchandise, 294, 4), `betawipe` (service-general, 260, 0), `קסיד` (merchandise, 245, 4), `בקיר` (fluids, 244, 7), `dhum` (engine, 225, 0), `dtivers` (accessories, 225, 2), `rundstreckenbroschure` (merchandise, 225, 9), `rework` (service-general, 224, 3), `סיכות` (wipers-washers, 222, 5), `talegator` (suspension-steering, 221, 0), `הרוק` (clutch-drivetrain, 220, 1), `לגונה` (brakes, 220, 11), `הדבקת` (wipers-washers, 216, 9), `בטחוניות` (merchandise, 215, 2), `כויל` (engine, 201, 9), `פיטמה` (wheels-bearings, 200, 3), `ihfa` (air-conditioning-heating, 192, 0), `ינשמ` (clutch-drivetrain, 187, 0), `תונולח` (clutch-drivetrain, 187, 1), `תלעפה` (clutch-drivetrain, 187, 1), `ברקט` (gearbox, 186, 10), `reillo` (fasteners, 185, 10), `טראק` (fasteners, 173, 8), `בקרץ` (gearbox, 172, 1), `ימרונפ` (clutch-drivetrain, 172, 4), `לצפר` (gearbox, 167, 2), `חיצוניתשמאל` (clutch-drivetrain, 160, 2), `םיבסימ` (fasteners, 157, 0), `duffle` (merchandise, 149, 2), `מינון` (fasteners, 148, 6), `adhesv` (fluids, 145, 1), `csj` (fluids, 145, 2), `בקבוקון` (merchandise, 144, 10), `ריסוק` (gearbox, 132, 11), `הנימי` (merchandise, 126, 1), `afls` (engine, 121, 5), `atvmt` (electrical, 121, 2), `לסונר` (clutch-drivetrain, 121, 5), `נושב` (wipers-washers, 121, 1), `הרונמ` (merchandise, 120, 3), `ריוא` (clutch-drivetrain, 118, 7), `holders` (wheels-bearings, 114, 7), `תפם` (gearbox, 110, 1), `אלל` (clutch-drivetrain, 108, 3), `איידלר` (body-exterior, 107, 5), `auszieher` (hybrid-ev, 106, 0), `spaser` (cooling, 104, 0), `פיאנו` (fasteners, 103, 2), `ilsqpm` (air-conditioning-heating, 102, 0), `נפצים` (brakes, 102, 5), `supercar` (engine, 101, 5), `פנץ` (fasteners, 101, 6), `הודי` (merchandise, 100, 2), `סוויטשרט` (merchandise, 100, 2), `גגש` (lighting, 97, 4), `rekirt` (fuel-air, 96, 0), `ilpx` (air-conditioning-heating, 95, 1), `cuv` (accessories, 91, 9), `qqc` (air-conditioning-heating, 91, 1), `easel` (lighting, 89, 2), `adpu` (engine, 84, 3), `סונר` (suspension-steering, 84, 10), `שנן` (interior-comfort, 84, 5), `maintanance` (merchandise, 80, 0), `קולנט` (wheels-bearings, 80, 1), `ניסאן` (fasteners, 78, 10), `תמוכה` (wipers-washers, 77, 3), `טפרלוק` (exhaust, 76, 6), `ריחן` (fuel-air, 74, 2), `להברקת` (body-exterior, 73, 0), `מסליה` (interior-comfort, 73, 2), `תומכן` (electrical, 73, 6), `דרישפט` (suspension-steering, 72, 5), `antenne` (body-exterior, 71, 5), `כליעבודה` (accessories, 71, 0), `caymann` (accessories, 70, 6), `lrheritagelg` (engine, 69, 2), `objekt` (filters, 69, 0), `לבטריה` (fasteners, 68, 7), `promotional` (merchandise, 67, 0), `תימינפ` (clutch-drivetrain, 65, 11), `flaring` (exhaust, 64, 0), `ihra` (filters, 61, 1), `otrs` (belts-chains, 61, 0), `בלנו` (fasteners, 60, 8), `לטכוגרף` (merchandise, 59, 2), `tatsomreht` (wheels-bearings, 58, 5), `disassembly` (service-general, 56, 6), `esirb` (body-exterior, 55, 0), `qpm` (air-conditioning-heating, 55, 0), `ralliuorb` (brakes, 54, 1), `מעולים` (belts-chains, 54, 2), `jci` (suspension-steering, 52, 6), `kvm` (engine, 52, 4), `טובלן` (merchandise, 52, 4), `ןגזמל` (clutch-drivetrain, 52, 2), `רוריק` (clutch-drivetrain, 52, 6), `cellphone` (accessories, 51, 4), `חולצות` (merchandise, 51, 1), `לפלרנטרים` (belts-chains, 51, 0), `סטארטר` (air-conditioning-heating, 49, 9), `navigtio` (audio-electronics, 48, 2), `sense` (exhaust, 48, 5), `וידוא` (merchandise, 48, 0), `חופמ` (clutch-drivetrain, 48, 5), `ללכמ` (clutch-drivetrain, 48, 7), `סנוכל` (clutch-drivetrain, 48, 1), `רבגמ` (merchandise, 48, 6), `qmc` (hybrid-ev, 47, 1), `דיאפרגמת` (fasteners, 47, 0), `rdar` (lighting, 46, 10), `ellemes` (fasteners, 45, 4), `strapthang` (engine, 44, 0), `ablegeboden` (interior-comfort, 43, 4), `ilhx` (hybrid-ev, 43, 0), `mjagknit` (wipers-washers, 43, 0), `mmm` (lighting, 43, 3), `wsj` (wipers-washers, 43, 1), `naps` (hybrid-ev, 42, 4), `spirengt` (gearbox, 42, 0), `גומחה` (fuel-air, 42, 11), `הפשרה` (filters, 42, 5), `בירגיה` (suspension-steering, 41, 0), `בעפנ` (gearbox, 41, 2), `צלינדרים` (exhaust, 41, 3), `מרפד` (fuel-air, 40, 0), `pedestals` (brakes, 39, 0), `אינדיקטור` (air-conditioning-heating, 39, 11), `dowager` (wheels-bearings, 38, 1), `חןטים` (wipers-washers, 38, 2), `מתען` (electrical, 38, 2), `קוחב` (gearbox, 38, 0), `sanitizer` (merchandise, 37, 9), `toddlert` (cooling, 37, 3), `מגלשים` (audio-electronics, 37, 1), `amn` (interior-comfort, 36, 7), `pntd` (fluids, 36, 7), `שסטום` (engine, 36, 7), `mandrel` (service-general, 35, 6), `ספורטז` (audio-electronics, 35, 7), `אורינגים` (merchandise, 34, 5), `זפת` (belts-chains, 34, 4), `לרב` (fasteners, 34, 9), `פרובים` (fasteners, 34, 0), `רגללים` (fasteners, 34, 0), `שונים` (fasteners, 34, 4), `iuorb` (body-exterior, 33, 6), `טוויח` (clutch-drivetrain, 33, 5), `מברשות` (air-conditioning-heating, 33, 2), `מגרד` (body-exterior, 33, 5), `schutzkappe` (fasteners, 32, 1), `לסינכרומישן` (safety-systems, 31, 1), `סרוומוטור` (wheels-bearings, 31, 0), `wcac` (fasteners, 30, 1), `דקאר` (electrical, 30, 1), `הניעט` (merchandise, 30, 8), `כיסןי` (suspension-steering, 30, 8), `מיקרוסוויץ` (electrical, 30, 10), `עקשל` (merchandise, 30, 2), `prestu` (fuel-air, 29, 3), `זוקינ` (clutch-drivetrain, 29, 3), `ניעלה` (merchandise, 29, 1), `etnaj` (fasteners, 28, 9), `maintenace` (service-general, 28, 2), `ענתמ` (suspension-steering, 28, 2), `יברזר` (suspension-steering, 27, 1), `מותניים` (audio-electronics, 27, 3), `צןפר` (merchandise, 27, 0), `beilagscheibe` (fluids, 26, 1), `ilvml` (hybrid-ev, 26, 0), `montagevorrichtung` (fluids, 26, 1), `evpi` (fasteners, 25, 1), `חתפמ` (merchandise, 25, 9), `לאיבוקס` (body-exterior, 25, 0), `binder` (service-general, 24, 5), `ilsqme` (air-conditioning-heating, 24, 2), `tvss` (hybrid-ev, 24, 1), `verschluss` (hybrid-ev, 24, 1), `selleib` (wipers-washers, 23, 2), `asist` (hybrid-ev, 22, 1), `gegenhalter` (hybrid-ev, 22, 0), `לולכמ` (clutch-drivetrain, 22, 9), `לסליב` (filters, 22, 4), `פזינקס` (wheels-bearings, 22, 5), `swoodish` (interior-comfort, 21, 1), `uhrenetui` (engine, 21, 0), `התאמה` (gearbox, 21, 11), `טכנוגרף` (interior-comfort, 21, 0), `לספירל` (fasteners, 21, 10), `קרולה` (fasteners, 21, 0), `ריליים` (gearbox, 21, 3), `תארמ` (clutch-drivetrain, 21, 1), `תילטיגיד` (clutch-drivetrain, 21, 1), `bullfrog` (hybrid-ev, 20, 9), `chaner` (hybrid-ev, 20, 0), `groups` (service-general, 20, 0), `hazord` (hybrid-ev, 20, 4), `גרומט` (exhaust, 20, 5), `דאוטיסיל` (exhaust, 20, 1), `sundry` (service-general, 19, 11), `ארובה` (exhaust, 19, 3), `גפוס` (clutch-drivetrain, 19, 4), `היגרנא` (clutch-drivetrain, 19, 4), `inmani` (exhaust, 18, 5), `mercs` (air-conditioning-heating, 18, 0), `הרפאמ` (merchandise, 18, 1), `acaf` (merchandise, 17, 7), `leitfaden` (clutch-drivetrain, 17, 1), `לבקבוק` (merchandise, 17, 5), `לונטיל` (exhaust, 17, 4), `ריממ` (clutch-drivetrain, 17, 6), `chng` (brakes, 16, 9), `טפס` (fasteners, 16, 3), `דאצ` (merchandise, 15, 10), `ינכמ` (merchandise, 15, 5), `לאדים` (gearbox, 15, 2), `ללנבה` (fasteners, 15, 0), `קונסילר` (fasteners, 15, 0), `mjagknitwsjk` (merchandise, 14, 0), `הנוילע` (clutch-drivetrain, 14, 5), `טרמופלנג` (merchandise, 14, 7), `לאבופורטור` (belts-chains, 14, 1), `לנדנדים` (air-conditioning-heating, 14, 9), `מגולוון` (fasteners, 14, 0), `מחורר` (fasteners, 14, 9), `סינכרומישן` (filters, 14, 10), `סכך` (wipers-washers, 14, 0), `פלואנס` (merchandise, 14, 4), `game` (service-general, 13, 6), `recepti` (service-general, 13, 4), `דיפראנציאל` (body-exterior, 13, 2), `הליסמ` (clutch-drivetrain, 13, 2), `לקונצנסור` (body-exterior, 13, 3), `סקרטינג` (clutch-drivetrain, 13, 1), `פיקדון` (wheels-bearings, 13, 9), `aadqj` (service-general, 12, 6), `etching` (audio-electronics, 12, 0), `ilhqmk` (air-conditioning-heating, 12, 1), `mbtl` (air-conditioning-heating, 12, 2), `גרוס` (suspension-steering, 12, 3), `לטווינגו` (fuel-air, 12, 1), `aufnahme` (hybrid-ev, 11, 3), `expense` (service-general, 11, 10), `feedback` (fluids, 11, 5), `hexbladeslo` (air-conditioning-heating, 11, 0), `ilhqml` (air-conditioning-heating, 11, 0), `insertion` (service-general, 11, 6), `kraftstoff` (filters, 11, 2), `miata` (service-general, 11, 6), `vorratsanzeige` (filters, 11, 1), `ייבוש` (wipers-washers, 11, 1), `crqo` (merchandise, 10, 1), `fial` (hybrid-ev, 10, 3), `vernte` (brakes, 10, 1), `vni` (body-exterior, 10, 1), `ארכונה` (interior-comfort, 10, 0), `המניע` (fasteners, 10, 6), `יביטקא` (clutch-drivetrain, 10, 1), `לבלפ` (electrical, 10, 1), `מרוח` (gearbox, 10, 8), `קיטבג` (gearbox, 10, 0), `onesie` (merchandise, 9, 10), `sanding` (service-general, 9, 9), `thundercloud` (body-exterior, 9, 4), `גונג` (fasteners, 9, 4), `משולה` (suspension-steering, 9, 1), `ellepuoc` (merchandise, 8, 2), `gingham` (merchandise, 8, 10), `ilsqqc` (air-conditioning-heating, 8, 0), `innensechskant` (hybrid-ev, 8, 1), `jok` (merchandise, 8, 0), `ornment` (filters, 8, 2), `prott` (interior-comfort, 8, 2), `זכרמ` (clutch-drivetrain, 8, 5), `מינור` (exhaust, 8, 3), `נפוח` (audio-electronics, 8, 2), `ספיקת` (filters, 8, 1), `עיין` (gearbox, 8, 1), `פיקות` (gearbox, 8, 2), `פלנז` (gearbox, 8, 0), `שסתמ` (fuel-air, 8, 2), `תותיא` (clutch-drivetrain, 8, 3), `adaptersatz` (electrical, 7, 0), `agrmt` (service-general, 7, 1), `brochures` (merchandise, 7, 8), `fastner` (electrical, 7, 10), `intensified` (audio-electronics, 7, 1), `microfiber` (accessories, 7, 8), `xvu` (service-general, 7, 2), `הבוהצ` (merchandise, 7, 1), `המילב` (clutch-drivetrain, 7, 5), `זויפ` (clutch-drivetrain, 7, 4), `יטלומ` (clutch-drivetrain, 7, 0), `לאוטומט` (merchandise, 7, 9), `ליעפמ` (clutch-drivetrain, 7, 0), `מחלק` (filters, 7, 5), `סביבון` (engine, 7, 5), `רעמ` (clutch-drivetrain, 7, 9), `תיוותו` (merchandise, 7, 1), `cok` (merchandise, 6, 1), `forming` (service-general, 6, 6), `forms` (service-general, 6, 3), `hcmr` (service-general, 6, 2), `intellisafe` (safety-systems, 6, 9), `jhp` (fasteners, 6, 6), `jig` (service-general, 6, 9), `klka` (service-general, 6, 2), `lhn` (service-general, 6, 3), `mvbm` (service-general, 6, 2), `snshd` (accessories, 6, 2), `toolbox` (accessories, 6, 7), `towhit` (accessories, 6, 9), `uyh` (clutch-drivetrain, 6, 7), `אנלוגי` (interior-comfort, 6, 2), `ברדס` (audio-electronics, 6, 8), `המפלג` (engine, 6, 0), `לחבור` (fuel-air, 6, 2), `מגןם` (body-exterior, 6, 1), `מוברז` (air-conditioning-heating, 6, 7), `מזיר` (clutch-drivetrain, 6, 1), `עונמל` (suspension-steering, 6, 9), `auxill` (clutch-drivetrain, 5, 9), `bbou` (merchandise, 5, 1), `blouson` (merchandise, 5, 9), `boxsters` (service-general, 5, 3), `brks` (brakes, 5, 8), `brthrs` (brakes, 5, 7), `dissipator` (cooling, 5, 11), `jsb` (suspension-steering, 5, 11), `kabelsatz` (electrical, 5, 7), `knocks` (engine, 5, 1), `megawarrior` (accessories, 5, 3), `pistion` (engine, 5, 0), `qmx` (service-general, 5, 2), `radhaus` (electrical, 5, 7), `school` (merchandise, 5, 9), `steerng` (audio-electronics, 5, 11), `sunburst` (body-exterior, 5, 3), `swich` (electrical, 5, 9), `tunic` (merchandise, 5, 6), `updating` (service-general, 5, 0), `vorne` (electrical, 5, 9), `working` (service-general, 5, 3), `בסורג` (body-exterior, 5, 9), `בערבית` (merchandise, 5, 5), `דדובמ` (clutch-drivetrain, 5, 1), `הבהוב` (lighting, 5, 7), `וואקם` (interior-comfort, 5, 1), `זיכוי` (wipers-washers, 5, 1), `לרפטד` (interior-comfort, 5, 0), `לרתמה` (lighting, 5, 2), `מכופפת` (engine, 5, 0), `סרוור` (clutch-drivetrain, 5, 4), `קולב` (engine, 5, 8), `רוטאוטקא` (clutch-drivetrain, 5, 0), `aahdx` (service-general, 4, 0), `abjgv` (fasteners, 4, 4), `aceite` (fluids, 4, 7), `aeaek` (service-general, 4, 1), `armkit` (brakes, 4, 0), `atomizer` (fuel-air, 4, 6), `bauer` (accessories, 4, 1), `beilagenblatt` (service-general, 4, 0), `belfm` (service-general, 4, 1), `broom` (accessories, 4, 3), `bubl` (interior-comfort, 4, 8), `butterwellte` (accessories, 4, 4), `cagoule` (merchandise, 4, 8), `circut` (accessories, 4, 3), `ckjhc` (service-general, 4, 0), `detailing` (accessories, 4, 6), `direkteinstieg` (service-general, 4, 0), `dud` (clutch-drivetrain, 4, 4), `eddie` (accessories, 4, 1), `empennage` (filters, 4, 1), `festival` (merchandise, 4, 4), `growh` (merchandise, 4, 7), `heritaget` (accessories, 4, 1), `hlcm` (service-general, 4, 4), `hxa` (fasteners, 4, 4), `idling` (fuel-air, 4, 10), `impos` (service-general, 4, 0), `intro` (service-general, 4, 11), `italian` (accessories, 4, 5), `kurzanle` (service-general, 4, 3), `kurzanleitung` (service-general, 4, 3), `lading` (service-general, 4, 0), `maintannace` (service-general, 4, 0), `married` (service-general, 4, 5), `mobdel` (fluids, 4, 8), `nnd` (service-general, 4, 6), `pcl` (service-general, 4, 7), `psmd` (service-general, 4, 2), `rgc` (service-general, 4, 1), `shank` (service-general, 4, 1), `sieve` (hybrid-ev, 4, 5), `sportz` (accessories, 4, 7), `stirling` (merchandise, 4, 6), `supplimental` (service-general, 4, 0), `supplir` (service-general, 4, 0), `ticket` (service-general, 4, 1), `travelling` (accessories, 4, 3), `tyvek` (accessories, 4, 1), `viud` (clutch-drivetrain, 4, 1), `vuen` (merchandise, 4, 2), `zusatzanleitung` (service-general, 4, 1), `טאכוגרף` (exhaust, 4, 0), `טינר` (interior-comfort, 4, 0), `לאימוביליזר` (merchandise, 4, 6), `לוקמר` (clutch-drivetrain, 4, 2), `לפדל` (audio-electronics, 4, 8), `רטיווטו` (clutch-drivetrain, 4, 0), `abanv` (service-general, 3, 0), `adapterkabel` (electrical, 3, 5), `aewp` (electrical, 3, 5), `afhfz` (service-general, 3, 0), `auflage` (service-general, 3, 1), `baaef` (service-general, 3, 2), `bbaek` (service-general, 3, 0), `bbhdx` (service-general, 3, 1), `bblby` (service-general, 3, 0), `bigfoot` (accessories, 3, 5), `butterwellcr` (service-general, 3, 1), `cancelli` (service-general, 3, 4), `ccaad` (service-general, 3, 1), `ccjgt` (service-general, 3, 3), `cluch` (service-general, 3, 5), `cnstr` (fluids, 3, 3), `combox` (accessories, 3, 4), `craphic` (merchandise, 3, 4), `dbndr` (service-general, 3, 1), `dchez` (service-general, 3, 5), `default` (service-general, 3, 0), `definitive` (merchandise, 3, 11), `diamaris` (wheels-bearings, 3, 6), `drehmomentschl` (service-general, 3, 0), `duble` (accessories, 3, 0), `eccm` (electrical, 3, 7), `edgepro` (fluids, 3, 7), `egsm` (electrical, 3, 8), `elektronic` (accessories, 3, 0), `extinguishing` (safety-systems, 3, 5), `fashion` (accessories, 3, 1), `fdlfn` (fasteners, 3, 3), `featuring` (accessories, 3, 1), `feefed` (accessories, 3, 0), `frskp` (service-general, 3, 0), `gaket` (gearbox, 3, 0), `galvanized` (fasteners, 3, 9), `growlerhoo` (merchandise, 3, 6), `gsnd` (accessories, 3, 0), `gudgeon` (engine, 3, 9), `guiding` (service-general, 3, 11), `gwp` (service-general, 3, 0), `herringbone` (belts-chains, 3, 4), `hhf` (service-general, 3, 3), `hsco` (service-general, 3, 1), `htno` (electrical, 3, 9), `ilhqmc` (air-conditioning-heating, 3, 1), `ilvn` (air-conditioning-heating, 3, 0), `jeb` (service-general, 3, 1), `knitted` (merchandise, 3, 6), `koozie` (accessories, 3, 0), `kzv` (service-general, 3, 0), `leaflet` (merchandise, 3, 4), `leggings` (merchandise, 3, 11), `lexuslink` (service-general, 3, 0), `lgy` (service-general, 3, 0), `lingu` (service-general, 3, 3), `lkas` (safety-systems, 3, 7), `longer` (service-general, 3, 6), `merrick` (accessories, 3, 1), `mobilspl` (fluids, 3, 3), `modling` (body-exterior, 3, 8), `monocoque` (body-exterior, 3, 7), `mro` (service-general, 3, 8), `multilingua` (service-general, 3, 2), `neutralizer` (fluids, 3, 5), `nibmoc` (fasteners, 3, 2), `obergurt` (body-exterior, 3, 3), `openend` (service-general, 3, 0), `ornamentation` (accessories, 3, 1), `parapet` (body-exterior, 3, 11), `pcmii` (audio-electronics, 3, 5), `positioner` (service-general, 3, 0), `ptrn` (accessories, 3, 1), `racerback` (merchandise, 3, 10), `rears` (exhaust, 3, 8), `reprint` (merchandise, 3, 6), `rescue` (safety-systems, 3, 10), `revcounter` (electrical, 3, 11), `roadshower` (accessories, 3, 0), `rsrl` (service-general, 3, 0), `ruetpmoc` (electrical, 3, 9), `ruetpur` (service-general, 3, 1), `sheepskin` (interior-comfort, 3, 3), `stories` (merchandise, 3, 4), `stydon` (body-exterior, 3, 11), `sweatshrt` (merchandise, 3, 6), `sxv` (suspension-steering, 3, 8), `tatsomreh` (cooling, 3, 11), `telescoping` (accessories, 3, 7), `tissue` (merchandise, 3, 10), `toddler` (merchandise, 3, 6), `transforce` (gearbox, 3, 6), `ujoint` (gearbox, 3, 8), `ujointkit` (wheels-bearings, 3, 8), `באנגלית` (merchandise, 3, 3), `בצדדים` (safety-systems, 3, 1), `בתעלות` (filters, 3, 3), `הוראה` (service-general, 3, 8), `הכיסאות` (interior-comfort, 3, 3), `טיימר` (engine, 3, 6), `טפרטורה` (wheels-bearings, 3, 4), `כמבל` (accessories, 3, 0), `כעבנ` (fuel-air, 3, 0), `להולכי` (safety-systems, 3, 6), `להעמסת` (accessories, 3, 0), `לזרם` (electrical, 3, 3), `למחפר` (service-general, 3, 6), `לנסיעות` (accessories, 3, 0), `לסלולר` (accessories, 3, 1), `לצוק` (service-general, 3, 1), `לקוסול` (body-exterior, 3, 2), `לריילה` (gearbox, 3, 1), `לשימס` (service-general, 3, 10), `לתליית` (accessories, 3, 4), `מישר` (electrical, 3, 8), `מקרופון` (audio-electronics, 3, 10), `משלוח` (service-general, 3, 1), `נשר` (accessories, 3, 1), `סטבילייזר` (suspension-steering, 3, 11), `סיטרכ` (merchandise, 3, 3), `עגולה` (accessories, 3, 8), `עניבה` (merchandise, 3, 4), `צרפתית` (merchandise, 3, 9), `קומפיוטר` (electrical, 3, 8), `קייס` (accessories, 3, 5), `רסממ` (electrical, 3, 8), `שאקל` (service-general, 3, 2), `תכליתית` (accessories, 3, 4)

## Errors (1)

The 2-character token hit the 30 s statement timeout. Trigram indexes cannot serve patterns shorter than 3 characters, so the query scans the whole table. It was not retried, to avoid a full-table scan on production.

- `פס` (body-exterior): DBAPIError: (sqlalchemy.dialects.postgresql.asyncpg.Error) <class 'asyncpg.exceptions.QueryCanceledError'>: canceling statement due 

## Owner console commands (WhatsApp owner console — NOT executed)

- Approve one word: `אשרמילה <word>` (refused by `approve()` unless the verdict is `ok`)
- Force-approve against the evidence: `אשרמילה <word> בכוח` (shows the real spread first)
- Reject one word: `דחהמילה <word>` (also undoes any parts it already moved)
- List what is waiting: `מילים`

Console limitation (verified in `agents/owner_console.py` around line 1656): `אשרמילה`/`דחהמילה` capture ONE word (`(\S+)`), so multi-word phrases cannot be approved or rejected through the console as it stands; also `category_learning.approve_as()` (file under a different category) has no console command. In THIS backlog there are 0 multi-word tokens, so the one-word limit does not block any of them today. Original note: the console regex captures ONE word (`(\S+)`), so multi-word phrases cannot be approved/rejected through it as-is. Multi-word `ok` tokens affected: 0

Example for the top `ok` tokens:

```
אשרמילה computer
אשרמילה damp
אשרמילה trans
אשרמילה adapter
אשרמילה buckle
אשרמילה fil
אשרמילה transm
אשרמילה gate
אשרמילה lift
אשרמילה inte
```
