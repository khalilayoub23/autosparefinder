import { Link } from 'react-router-dom'
import { FileText } from 'lucide-react'

export default function Terms() {
  return (
    <div className="min-h-screen bg-gray-50" dir="rtl">
      {/* Header */}
      <div className="bg-orange-600 text-white py-10 px-4">
        <div className="max-w-3xl mx-auto flex items-center gap-3">
          <FileText className="w-8 h-8 flex-shrink-0" />
          <div>
            <h1 className="text-2xl font-bold">תקנון שימוש - Auto Spare</h1>
            <p className="text-orange-100 text-sm mt-1">תאריך עדכון אחרון: 28 בפברואר 2026 · גרסה: 1.0</p>
          </div>
        </div>
      </div>

      <div className="max-w-3xl mx-auto px-4 py-10 space-y-6">

        {/* Business Info */}
        <section className="bg-orange-50 border border-orange-200 rounded-xl p-5 text-sm text-gray-700 space-y-1">
          <p><strong>עוסק מורשה:</strong> 060633880</p>
          <p><strong>כתובת:</strong> הרצל 55, עכו</p>
          <p><strong>אתר:</strong> autospare.com</p>
          <p><strong>דוא&quot;ל:</strong> <a href="mailto:support@autospare.com" className="text-orange-600 underline">support@autospare.com</a></p>
        </section>

        {/* TOC */}
        <section className="bg-white rounded-xl p-6 shadow-sm">
          <h2 className="text-base font-bold text-gray-900 mb-3">תוכן עניינים</h2>
          <ol className="list-decimal list-inside space-y-1 text-sm text-orange-600">
            {['הגדרות','כללי','הרשמה וחשבון משתמש','שימוש באתר','מחירים ותשלומים',
              'הזמנות ואספקה','אחריות והחזרות','קניין רוחני','הגבלת אחריות',
              'פרטיות ואבטחת מידע','שינויים בתקנון','דין וסמכות שיפוט'].map((t,i) => (
              <li key={i}><a href={`#s${i+1}`} className="hover:underline">{t}</a></li>
            ))}
          </ol>
        </section>

        <S id="s1" title="1. הגדרות">
          <ul className="space-y-2 text-gray-700 text-sm">
            <li><strong>&quot;החברה&quot; / &quot;אנו&quot;</strong> — Auto Spare, עוסק מורשה 060633880</li>
            <li><strong>&quot;האתר&quot;</strong> — autospare.com וכל תת-דומיינים</li>
            <li><strong>&quot;המשתמש&quot; / &quot;הלקוח&quot; / &quot;אתה&quot;</strong> — כל אדם המשתמש באתר</li>
            <li><strong>&quot;המוצרים&quot;</strong> — חלקי חילוף ואביזרים לרכב</li>
            <li><strong>&quot;השירות&quot;</strong> — כלל השירותים המוצעים באתר</li>
            <li><strong>&quot;התקנון&quot;</strong> — מסמך זה</li>
            <li><strong>&quot;ההזמנה&quot;</strong> — בקשת רכישה של מוצר/ים</li>
            <li><strong>&quot;סוכני AI&quot;</strong> — מערכות בינה מלאכותית המספקות שירות</li>
          </ul>
        </S>

        <S id="s2" title="2. כללי">
          <div className="space-y-4 text-sm text-gray-700">
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">2.1 הסכמה לתקנון</h3>
              <p>השימוש באתר מהווה הסכמה מלאה ובלתי מסויגת לכל תנאי התקנון. <strong>אם אינך מסכים לתקנון — אל תשתמש באתר.</strong></p>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">2.2 גיל מינימום</h3>
              <p>השימוש באתר מיועד לבני 18 ומעלה בלבד. על ידי שימוש באתר, אתה מצהיר שהינך בן 18 לפחות.</p>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">2.3 שימוש מורשה</h3>
              <ul className="space-y-1">
                <li>✅ <strong>פרטי</strong> — רכישה לשימוש אישי</li>
                <li>✅ <strong>מסחרי</strong> — מוסכים, עסקים, מפעלים</li>
                <li>✅ <strong>רכישה חוזרת (Resale)</strong> — מותר למכור הלאה</li>
              </ul>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">2.4 שימוש אסור</h3>
              <ul className="space-y-1">
                <li>❌ פריצה, מניפולציה או פגיעה באתר</li>
                <li>❌ העתקת תוכן, עיצוב או קוד</li>
                <li>❌ שימוש ברובוטים/scrapers ללא אישור</li>
                <li>❌ פרסום תוכן בלתי חוקי או פוגעני</li>
                <li>❌ התחזות למשתמש אחר או לחברה</li>
              </ul>
            </div>
          </div>
        </S>

        <S id="s3" title="3. הרשמה וחשבון משתמש">
          <div className="space-y-4 text-sm text-gray-700">
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">3.1 יצירת חשבון</h3>
              <p>להשתמש בשירותים מסוימים נדרשת הרשמה. עליך לספק: שם מלא, כתובת דוא&quot;ל תקפה, מספר טלפון, וסיסמה חזקה (8+ תווים, מספרים ואותיות).</p>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">3.2 אימות</h3>
              <ul className="space-y-1">
                <li><strong>2FA (אימות דו-שלבי)</strong> — SMS לטלפון נייד (חובה)</li>
                <li><strong>אימות דוא&quot;ל</strong> — קישור אימות</li>
                <li><strong>אימות נוסף</strong> — במקרים של עסקאות גדולות</li>
              </ul>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">3.3 אחריות המשתמש</h3>
              <ul className="space-y-1">
                <li>✅ שמירת סודיות פרטי ההתחברות</li>
                <li>✅ כל פעילות בחשבונך</li>
                <li>✅ עדכון פרטים במקרה של שינוי</li>
                <li>⚠️ להודיע מיד על שימוש לא מורשה</li>
              </ul>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">3.4 השעיה וסגירת חשבון</h3>
              <p>החברה רשאית להשעות או לסגור חשבון במקרים של: הפרת תנאי התקנון, פעילות חשודה או הונאה, אי-תשלום, שימוש לא חוקי.</p>
            </div>
          </div>
        </S>

        <S id="s4" title="4. שימוש באתר">
          <div className="space-y-4 text-sm text-gray-700">
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">4.1 סוכני AI</h3>
              <p className="mb-2">האתר משתמש ב-<strong>10 סוכני בינה מלאכותית</strong> לשירות לקוחות: חיפוש והמלצה על חלקים, תמיכה טכנית, ניהול הזמנות, שירות לקוחות 24/7.</p>
              <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 text-yellow-800 text-xs">
                <p className="font-semibold mb-1">חשוב לדעת:</p>
                <ul className="space-y-1">
                  <li>• הסוכנים לומדים ומשתפרים</li>
                  <li>• אין תחליף לייעוץ מקצועי של מכונאי</li>
                  <li>• החברה אינה אחראית להחלטות על סמך המלצות AI בלבד</li>
                </ul>
              </div>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">4.2 מידע על מוצרים</h3>
              <p>המידע באתר הוא לידיעה בלבד, עשוי להשתנות ללא הודעה, ועשוי להכיל אי-דיוקים לא מכוונים. <strong>על הלקוח לוודא התאמה לפני רכישה.</strong></p>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">4.3 זמינות המוצרים</h3>
              <ul className="space-y-1">
                <li>• המלאי מתעדכן בזמן אמת אך עשוי להשתנות</li>
                <li><strong>מוצרים &quot;במלאי&quot;</strong> — זמינים לרכישה מיידית</li>
                <li><strong>מוצרים &quot;לפי הזמנה&quot;</strong> — 7-21 ימי אספקה</li>
                <li>• החברה רשאית לבטל הזמנה אם המוצר לא זמין</li>
              </ul>
            </div>
          </div>
        </S>

        <S id="s5" title="5. מחירים ותשלומים">
          <div className="space-y-4 text-sm text-gray-700">
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">5.1 מחירים</h3>
              <p className="mb-2">כל המחירים באתר הם ב-<strong>שקלים חדשים (₪)</strong>.</p>
              <div className="bg-amber-50 border border-amber-200 rounded-lg p-3 text-xs space-y-1 text-amber-800">
                <p>⚠️ <strong>המחיר המוצג אינו כולל מע&quot;מ (17%) ודמי משלוח.</strong></p>
                <p>מע&quot;מ ודמי משלוח יתווספו בעמוד התשלום.</p>
              </div>
              <div className="bg-gray-100 rounded-lg p-3 font-mono text-xs mt-2 space-y-1">
                <p>מחיר מוצר באתר:  ₪500</p>
                <p>מע&quot;מ (17%):       ₪85</p>
                <p>משלוח:            ₪91</p>
                <p className="border-t border-gray-300 pt-1 font-bold">סה&quot;כ לתשלום:   ₪676</p>
              </div>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">5.2 שינויי מחירים</h3>
              <p>המחירים עשויים להשתנות ללא הודעה. <strong>המחיר הקובע</strong> הוא בעת ביצוע ההזמנה (לא בעת הוספה לעגלה). שגיאות תמחור — החברה רשאית לבטל הזמנה.</p>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">5.3 אמצעי תשלום</h3>
              <ul className="space-y-1">
                <li>💳 <strong>כרטיסי אשראי</strong> (Visa, Mastercard, Amex) — דרך Stripe</li>
                <li>💳 <strong>Apple Pay / Google Pay</strong></li>
              </ul>
              <p className="text-gray-500 mt-1 text-xs">בעתיד: PayPal, bit, העברה בנקאית</p>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">5.4 אבטחת תשלומים</h3>
              <ul className="space-y-1">
                <li>🔒 כל התשלומים מוצפנים (SSL/TLS)</li>
                <li>🔒 אנו <strong>לא שומרים</strong> פרטי כרטיס אשראי</li>
                <li>🔒 תשלומים מעובדים ע&quot;י <strong>Stripe</strong> (תקן PCI DSS)</li>
              </ul>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">5.5 חשבונית מס</h3>
              <p>לאחר תשלום תקבל חשבונית מס/קבלה בדוא&quot;ל (PDF) עם פירוט מלא של הרכישה.</p>
            </div>
          </div>
        </S>

        <S id="s6" title="6. הזמנות ואספקה">
          <div className="space-y-4 text-sm text-gray-700">
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">6.1 תהליך ההזמנה</h3>
              <ol className="list-decimal list-inside space-y-1">
                <li>הוספה לעגלה — בחירת מוצרים</li>
                <li>פרטי משלוח — כתובת מלאה</li>
                <li>תשלום — דרך Stripe</li>
                <li>אישור — מייל אישור + חשבונית</li>
                <li>עיבוד — תוך שעה (הזמנה לספק)</li>
                <li>משלוח — 7-21 ימי עסקים</li>
                <li>מעקב — מספר tracking</li>
              </ol>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">6.2 זמני אספקה</h3>
              <div className="overflow-x-auto">
                <table className="w-full text-sm text-right border-collapse">
                  <thead>
                    <tr className="bg-orange-50 text-orange-700">
                      <th className="p-2 border border-gray-200">מקור</th>
                      <th className="p-2 border border-gray-200">זמן משוער</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[['ישראל','2-5 ימי עסקים'],['אירופה','7-14 ימי עסקים'],['ארה"ב','10-21 ימי עסקים'],['סין','14-30 ימי עסקים']].map(([src,t],i) => (
                      <tr key={i} className={i%2===1?'bg-gray-50':''}>
                        <td className="p-2 border border-gray-200 font-medium">{src}</td>
                        <td className="p-2 border border-gray-200">{t}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="text-gray-500 mt-2 text-xs">⚠️ זמנים משוערים בלבד — עיכובים אפשריים במכס, חגים, מזג אוויר.</p>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">6.4–6.6 מעקב, כתובת ואי-קבלה</h3>
              <ul className="space-y-1">
                <li>• תקבל מספר מעקב תוך 24-48 שעות</li>
                <li>• וודא נכונות הכתובת — <strong>אין אחריות לטעויות בכתובת</strong> שמסר הלקוח</li>
                <li>• שינוי כתובת אחרי הזמנה — אפשרי רק תוך 2 שעות</li>
                <li>• חבילה שלא הגיעה תוך 25 ימים (אירופה/ארה&quot;ב) או 35 ימים (סין) — צור קשר</li>
              </ul>
            </div>
          </div>
        </S>

        <S id="s7" title="7. אחריות והחזרות">
          <div className="space-y-3 text-sm text-gray-700">
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">7.1 אחריות</h3>
              <ul className="space-y-1">
                <li>• כל מוצר מגיע עם אחריות מקורית של היצרן (6-24 חודשים)</li>
                <li>• טיפול באחריות — דרך החברה</li>
                <li className="text-gray-500">לא באחריות: נזקי התקנה שגויה, שימוש לא נכון, בלאי רגיל</li>
              </ul>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">7.2 החזרות</h3>
              <ul className="space-y-1">
                <li>✅ 14 ימים לביטול (חוק הגנת הצרכן)</li>
                <li>✅ באריזה מקורית + ללא שימוש</li>
                <li>✅ זיכוי תוך 7-14 ימים</li>
              </ul>
              <p className="mt-2 text-xs">
                לפירוט מלא ראה{' '}
                <Link to="/refund" className="text-orange-600 underline font-semibold">מדיניות ביטולים והחזרות</Link>.
              </p>
            </div>
          </div>
        </S>

        <S id="s8" title="8. קניין רוחני">
          <div className="space-y-2 text-sm text-gray-700">
            <p>כל התוכן באתר (טקסט, עיצוב, לוגו, קוד, תמונות) — © 2026 Auto Spare. <strong>אסור להעתיק ללא אישור בכתב.</strong></p>
            <p>&quot;Auto Spare&quot; הוא סימן מסחר רשום. שמות ולוגואים של יצרנים שייכים לבעליהם.</p>
            <p>אתה מקבל רישיון <strong>מוגבל, אישי, לא-בלעדי</strong> לצפות בתוכן ולרכוש מוצרים בלבד.</p>
          </div>
        </S>

        <S id="s9" title="9. הגבלת אחריות">
          <div className="space-y-2 text-sm text-gray-700">
            <p>האתר והשירות ניתנים <strong>&quot;AS IS&quot;</strong> ללא אחריות לזמינות רציפה, נטול באגים, או תוצאות ספציפיות.</p>
            <p><strong>החברה לא תהיה אחראית ל:</strong> נזקים עקיפים, תוצאתיים, מקריים, אובדן רווחים או נתונים, פעולות צד ג׳ (ספקים, שליחים), החלטות שנעשו על סמך מידע באתר.</p>
            <p>הסכום המקסימלי מוגבל לסכום ששילמת עבור המוצר הספציפי.</p>
            <p>החברה משמשת כמתאם בלבד — הספקים אחראים לאיכות, אספקה ואחריות.</p>
          </div>
        </S>

        <S id="s10" title="10. פרטיות ואבטחת מידע">
          <div className="space-y-2 text-sm text-gray-700">
            <p>השימוש באתר כפוף גם ל<Link to="/privacy" className="text-orange-600 underline font-semibold">מדיניות הפרטיות</Link> שלנו.</p>
            <ul className="space-y-1">
              <li>🔒 <strong>הצפנה מלאה</strong> (SSL/TLS 256-bit)</li>
              <li>🔒 <strong>2FA חובה</strong> לכל החשבונות</li>
              <li>🔒 שרתים מאובטחים וגיבויים יומיים</li>
            </ul>
            <p>במקרה של פריצת אבטחה — הודעה תוך <strong>72 שעות</strong> ודיווח לרשות להגנת הפרטיות.</p>
          </div>
        </S>

        <S id="s11" title="11. שינויים בתקנון">
          <div className="text-sm text-gray-700 space-y-2">
            <p>החברה רשאית לשנות תקנון זה בכל עת. שינויים מהותיים יפורסמו 14 יום מראש בדוא&quot;ל, באתר ובהתראה בחשבון.</p>
            <p>המשך שימוש לאחר כניסת השינויים לתוקף = הסכמה לתקנון המעודכן.</p>
          </div>
        </S>

        <S id="s12" title="12. דין וסמכות שיפוט">
          <div className="text-sm text-gray-700 space-y-2">
            <p>תקנון זה כפוף לדיני מדינת ישראל. <strong>סמכות ייחודית</strong> לבתי המשפט במחוז צפון (חיפה/עכו).</p>
            <p>הגרסה <strong>העברית</strong> היא הקובעת. במקרה של סתירה — הפרשנות לטובת הצרכן כחוק.</p>
          </div>
        </S>

        {/* Contact */}
        <section className="bg-white rounded-xl p-6 shadow-sm">
          <h2 className="text-base font-bold text-gray-900 mb-3 border-b border-orange-100 pb-2">יצירת קשר</h2>
          <div className="text-sm text-gray-700 space-y-1">
            <p>📧 <strong>דוא&quot;ל:</strong> <a href="mailto:legal@autospare.com" className="text-orange-600 underline">legal@autospare.com</a></p>
            <p>📱 <strong>טלפון:</strong> 04-1234567</p>
            <p>🏢 <strong>כתובת:</strong> הרצל 55, עכו</p>
            <p>🌐 <strong>אתר:</strong> autospare.com</p>
            <p className="text-gray-500">שעות פעילות: א׳-ה׳ 9:00-18:00</p>
          </div>
        </section>

        {/* Confirmation */}
        <section className="bg-green-50 border border-green-200 rounded-xl p-6 shadow-sm">
          <h2 className="text-base font-bold text-gray-900 mb-3">✅ אישור והסכמה</h2>
          <p className="text-sm text-gray-600 mb-3">על ידי שימוש באתר, אתה מאשר ומסכים:</p>
          <ul className="space-y-1 text-sm text-gray-700">
            <li>✅ קראתי והבנתי את תנאי התקנון</li>
            <li>✅ אני מסכים לכל תנאי התקנון</li>
            <li>✅ אני בן 18 ומעלה</li>
            <li>✅ הפרטים שמסרתי נכונים ומדויקים</li>
            <li>✅ אני מבין שמע&quot;מ ומשלוח נוספים למחיר</li>
          </ul>
        </section>

        <p className="text-center text-xs text-gray-400 pb-4">
          תאריך עדכון אחרון: 28 בפברואר 2026 · גרסה: 1.0<br />
          © 2026 Auto Spare. כל הזכויות שמורות.
        </p>
      </div>
    </div>
  )
}

function S({ id, title, children }) {
  return (
    <section id={id} className="bg-white rounded-xl p-6 shadow-sm scroll-mt-4">
      <h2 className="text-base font-bold text-gray-900 mb-3 border-b border-orange-100 pb-2">
        {title}
      </h2>
      {children}
    </section>
  )
}
