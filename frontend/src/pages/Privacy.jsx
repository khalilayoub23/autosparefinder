import { Link } from 'react-router-dom'
import { ShieldCheck } from 'lucide-react'

export default function Privacy() {
  return (
    <div className="min-h-screen bg-gray-50" dir="rtl">
      {/* Header */}
      <div className="bg-orange-600 text-white py-10 px-4">
        <div className="max-w-3xl mx-auto flex items-center gap-3">
          <ShieldCheck className="w-8 h-8 flex-shrink-0" />
          <div>
            <h1 className="text-2xl font-bold">מדיניות פרטיות - Auto Spare</h1>
            <p className="text-orange-100 text-sm mt-1">תאריך עדכון אחרון: 28 בפברואר 2026 · גרסה: 1.0</p>
            <p className="text-orange-200 text-xs mt-0.5">תואמת: תיקון 13 לחוק הגנת הפרטיות, התשמ&quot;א-1981 · עוסק מורשה: 060633880</p>
          </div>
        </div>
      </div>

      <div className="max-w-3xl mx-auto px-4 py-10 space-y-6">

        {/* TOC */}
        <section className="bg-white rounded-xl p-6 shadow-sm">
          <h2 className="text-base font-bold text-gray-900 mb-3">תוכן עניינים</h2>
          <ol className="list-decimal list-inside space-y-1 text-sm text-orange-600 columns-2">
            {['מבוא','מידע שאנו אוספים','כיצד אנו משתמשים במידע',
              'שיתוף עם צדדים שלישיים','אבטחת מידע','תקופות שמירה',
              'זכויותיך','קטינים','עוגיות וטכנולוגיות מעקב',
              'פריצת אבטחה','שינויים במדיניות','יצירת קשר'].map((t,i) => (
              <li key={i}><a href={`#p${i+1}`} className="hover:underline">{t}</a></li>
            ))}
          </ol>
        </section>

        <S id="p1" title="1. מבוא">
          <div className="space-y-3 text-sm text-gray-700">
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">1.1 התחייבות לפרטיות</h3>
              <p>ב-<strong>Auto Spare</strong> אנו מכבדים את פרטיותך ומתחייבים להגן על המידע האישי שלך בהתאם לחוק הגנת הפרטיות, התשמ&quot;א-1981 ותיקון 13.</p>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">1.2 מה כולל מסמך זה?</h3>
              <ul className="space-y-1">
                <li>✅ איזה מידע אנו אוספים</li>
                <li>✅ למה אנו משתמשים במידע</li>
                <li>✅ עם מי אנו משתפים</li>
                <li>✅ כיצד אנו מגנים על המידע</li>
                <li>✅ מה הזכויות שלך</li>
              </ul>
            </div>
            <p>השימוש באתר מהווה הסכמה למדיניות פרטיות זו. <strong>אם אינך מסכים — אל תשתמש באתר.</strong></p>
          </div>
        </S>

        <S id="p2" title="2. מידע שאנו אוספים">
          <div className="space-y-4 text-sm text-gray-700">
            <div>
              <h3 className="font-semibold text-gray-800 mb-2">2.1 מידע שאתה מספק</h3>
              <p className="font-medium text-gray-700 mb-1">בעת הרשמה:</p>
              <ul className="space-y-1 mb-3">
                <li>👤 שם מלא</li>
                <li>📧 כתובת דוא&quot;ל</li>
                <li>📱 מספר טלפון נייד (לאימות 2FA)</li>
                <li>🔒 סיסמה (מוצפנת)</li>
              </ul>
              <p className="font-medium text-gray-700 mb-1">בעת הזמנה:</p>
              <ul className="space-y-1 mb-3">
                <li>🏠 כתובת מלאה למשלוח</li>
                <li>🏢 שם חברה (אופציונלי)</li>
                <li>📋 ח.פ / ע.מ (לעסקים)</li>
              </ul>
              <p className="font-medium text-gray-700 mb-1">בעת שימוש:</p>
              <ul className="space-y-1">
                <li>🚗 פרטי רכב (אופציונלי — לשיפור שירות)</li>
                <li>💬 תוכן שיחות עם סוכני AI</li>
                <li>📸 תמונות שהעלית (לזיהוי חלקים)</li>
                <li>🎤 הקלטות קול (אם השתמשת בשירות קולי)</li>
                <li>🎥 קטעי וידאו (אם העלית)</li>
              </ul>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-2">2.2 מידע שנאסף אוטומטית</h3>
              <ul className="space-y-1 mb-3">
                <li>🌐 כתובת IP</li>
                <li>🖥️ סוג דפדפן ומכשיר</li>
                <li>📊 מערכת הפעלה</li>
                <li>🕐 זמן ומשך ביקור</li>
                <li>📄 עמודים שצפית בהם</li>
                <li>🔗 מקור ההפניה (איך הגעת לאתר)</li>
              </ul>
              <p className="font-medium text-gray-700 mb-1">עוגיות (Cookies):</p>
              <ul className="space-y-1">
                <li>🍪 עוגיות הכרחיות (אימות, עגלה)</li>
                <li>🍪 עוגיות סטטיסטיקה (Google Analytics)</li>
                <li>🍪 עוגיות שיווק (ניתנות לביטול)</li>
              </ul>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">2.3 מידע מצדדים שלישיים</h3>
              <ul className="space-y-1">
                <li>• כניסה דרך Google/Facebook — נקבל שם, דוא&quot;ל, תמונת פרופיל (באישורך)</li>
                <li>🚗 פרטי רכב מ-API משרד הרישוי (אם הזנת מספר רישוי)</li>
              </ul>
            </div>
          </div>
        </S>

        <S id="p3" title="3. כיצד אנו משתמשים במידע">
          <div className="space-y-4 text-sm text-gray-700">
            <div className="grid grid-cols-1 gap-3">
              {[
                ['✅ לספק את השירות', ['עיבוד הזמנות','משלוח מוצרים','יצירת קשר בנוגע להזמנה','שירות לקוחות']],
                ['✅ לשפר את השירות', ['התאמה אישית (המלצות חלקים)','ניתוח שימוש','שיפור סוכני AI','תיקון באגים']],
                ['✅ אבטחה', ['מניעת הונאות','זיהוי פעילות חשודה','אימות זהות (2FA)']],
                ['✅ שיווק (רק באישורך)', ['ניוזלטר','הצעות מותאמות אישית','מבצעים']],
                ['✅ חובות חוקיות', ['חשבונאות (7 שנים)','מס הכנסה','דיווח לרשויות (לפי צו בית משפט)']],
              ].map(([title, items], i) => (
                <div key={i} className="bg-gray-50 rounded-lg p-3">
                  <p className="font-semibold text-gray-800 mb-1">{title}</p>
                  <ul className="space-y-0.5">
                    {items.map((item, j) => <li key={j} className="text-gray-600">• {item}</li>)}
                  </ul>
                </div>
              ))}
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-2">3.2 AI ולמידת מכונה</h3>
              <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-blue-800 text-xs space-y-1">
                <p>• סוכני ה-AI לומדים מהשיחות שלך לשיפור השירות</p>
                <p>• <strong>לא משתפים</strong> מידע אישי עם מודלי AI חיצוניים</p>
                <p>• כל העיבוד נעשה ב<strong>סביבה מאובטחת</strong></p>
              </div>
              <div className="bg-gray-100 rounded-lg p-3 font-mono text-xs mt-2 space-y-1">
                <p className="text-green-700">✅ AI לומד: &quot;רכבי טויוטה קורולה צריכים פילטר שמן מסוג X&quot;</p>
                <p className="text-red-600">❌ AI לא שומר: &quot;יוסי כהן מרח׳ הרצל 5 הזמין...&quot;</p>
              </div>
            </div>
          </div>
        </S>

        <S id="p4" title="4. שיתוף מידע עם צדדים שלישיים">
          <div className="space-y-4 text-sm text-gray-700">
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">4.1 לא נמכיר את המידע</h3>
              <ul className="space-y-1">
                <li>❌ לא נמכור מידע למפרסמים</li>
                <li>❌ לא נשכיר רשימות תפוצה</li>
                <li>❌ לא נשתף עם מתחרים</li>
              </ul>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">4.2 חובות חוקיות</h3>
              <p>נשתף מידע רק אם: צו בית משפט מחייב, חקירה פלילית, הגנה על זכויותינו, או מניעת נזק מיידי.</p>
            </div>
          </div>
        </S>

        <S id="p5" title="5. אבטחת מידע">
          <div className="space-y-4 text-sm text-gray-700">
            <div>
              <h3 className="font-semibold text-gray-800 mb-2">5.1 אמצעי אבטחה טכניים</h3>
              <div className="grid grid-cols-2 gap-2">
                {[
                  ['🔒 הצפנה', ['SSL/TLS 256-bit לכל התקשורת','הצפנת מסד נתונים (AES-256)','הצפנת גיבויים']],
                  ['🔒 אימות', ['2FA חובה (SMS)','bcrypt hashing (12 rounds)','Device trust (6 חודשים)']],
                  ['🔒 רשת', ['Firewall מתקדם','DDoS protection','Rate limiting']],
                  ['🔒 גישה', ['הרשאות מוגבלות לעובדים','לוגים מלאים','ביקורת אבטחה רבעונית']],
                ].map(([title, items], i) => (
                  <div key={i} className="bg-gray-50 rounded-lg p-3">
                    <p className="font-semibold text-xs text-gray-800 mb-1">{title}</p>
                    <ul className="space-y-0.5 text-xs text-gray-600">
                      {items.map((item, j) => <li key={j}>• {item}</li>)}
                    </ul>
                  </div>
                ))}
              </div>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">5.2 אמצעי אבטחה ארגוניים</h3>
              <ul className="space-y-1">
                <li>👥 הכשרת אבטחת מידע לעובדים + הסכמי סודיות</li>
                <li>📋 מדיניות סיסמאות + עדכוני אבטחה שבועיים</li>
              </ul>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">5.3 גיבויים</h3>
              <ul className="space-y-1">
                <li>💾 גיבוי יומי מוצפן באחסון מרוחק</li>
                <li>💾 בדיקת שחזור חודשית</li>
              </ul>
            </div>
          </div>
        </S>

        <S id="p6" title="6. תקופות שמירה">
          <div className="space-y-3 text-sm text-gray-700">
            <div className="overflow-x-auto">
              <table className="w-full text-xs text-right border-collapse">
                <thead>
                  <tr className="bg-orange-50 text-orange-700">
                    <th className="p-2 border border-gray-200">סוג מידע</th>
                    <th className="p-2 border border-gray-200">תקופה</th>
                    <th className="p-2 border border-gray-200">סיבה</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    ['חשבוניות ותשלומים','7 שנים','חוק עסקאות גופים ציבוריים'],
                    ['הזמנות','5 שנים','התיישנות תביעות'],
                    ['שיחות עם AI','90 יום','שיפור שירות'],
                    ['תמונות/קול/וידאו','30 יום','זיהוי חלקים'],
                    ['לוגים טכניים','180 יום','אבטחה'],
                    ['חשבון משתמש פעיל','עד סגירה','שירות רצוף'],
                    ['חשבון לא פעיל','3 שנים','ארכיון'],
                  ].map(([t,p,r], i) => (
                    <tr key={i} className={i%2===1?'bg-gray-50':''}>
                      <td className="p-2 border border-gray-200 font-medium">{t}</td>
                      <td className="p-2 border border-gray-200">{p}</td>
                      <td className="p-2 border border-gray-200 text-gray-500">{r}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">6.2 מחיקה אוטומטית</h3>
              <ul className="space-y-1 text-xs">
                <li>• 30 יום — תמונות/קול שלא בשימוש</li>
                <li>• 90 יום — שיחות ישנות</li>
                <li>• 3 שנים — חשבונות לא פעילים (אחרי התראה)</li>
              </ul>
            </div>
          </div>
        </S>

        <S id="p7" title="7. זכויותיך">
          <div className="space-y-3 text-sm text-gray-700">
            <p className="text-xs text-gray-500">בהתאם לחוק הגנת הפרטיות (תיקון 13):</p>
            {[
              ['7.1 זכות עיון 👁️', 'לראות איזה מידע יש עליך. פנה דרך ״הגדרות → המידע שלי״ או privacy@autospare.com. מענה תוך 21 ימים (חינם פעם בשנה).'],
              ['7.2 זכות לתיקון ✏️', 'לתקן מידע שגוי דרך ״הגדרות → ערוך פרופיל״. מענה תוך 7 ימים.'],
              ['7.3 זכות למחיקה 🗑️', 'למחוק את המידע שלך דרך ״הגדרות → מחק חשבון״. לא ניתן למחוק חשבוניות (7 שנים — חובה חוקית). שאר המידע יימחק תוך 30 יום.'],
              ['7.4 זכות להגבלת שימוש 🚫', 'להגביל שיווק דרך ״הגדרות → העדפות תקשורת״.'],
              ['7.5 זכות להתנגדות ✋', 'לביטול שיווק — קישור ״הסר מרשימה״ בכל מייל. פעולה מיידית.'],
              ['7.6 זכות לניידות נתונים 📦', 'לקבל העתק של המידע (JSON/CSV) דרך ״הגדרות → ייצוא נתונים״. תוך 7 ימים.'],
            ].map(([title, desc], i) => (
              <div key={i} className="bg-gray-50 rounded-lg p-3">
                <p className="font-semibold text-gray-800 text-xs mb-1">{title}</p>
                <p className="text-xs text-gray-600">{desc}</p>
              </div>
            ))}

          </div>
        </S>

        <S id="p8" title="8. קטינים">
          <div className="text-sm text-gray-700 space-y-2">
            <p>האתר מיועד לבני <strong>18 ומעלה</strong> בלבד. אנו <strong>לא אוספים במכוון</strong> מידע מקטינים.</p>
            <p>אם התברר שנאסף מידע של קטין — נמחק מיד, נודיע להורים ונחסום את החשבון.</p>
          </div>
        </S>

        <S id="p9" title="9. עוגיות וטכנולוגיות מעקב">
          <div className="space-y-3 text-sm text-gray-700">
            <p className="text-xs text-gray-500">עוגיות (Cookies) הן קבצי טקסט קטנים שנשמרים במכשיר שלך.</p>
            <div className="grid grid-cols-2 gap-2">
              {[
                ['🍪 הכרחיות (חובה)', ['זכירת התחברות','עגלת קניות','העדפות שפה','אבטחה']],
                ['🍪 פונקציונליות', ['זכירת העדפות','התאמה אישית']],
                ['🍪 אנליטיקס', ['Google Analytics','ספירת מבקרים','דפים פופולריים']],
                ['🍪 שיווק', ['פרסום ממוקד','רימרקטינג']],
              ].map(([title, items], i) => (
                <div key={i} className="bg-gray-50 rounded-lg p-3">
                  <p className="font-semibold text-xs text-gray-800 mb-1">{title}</p>
                  <ul className="space-y-0.5 text-xs text-gray-600">
                    {items.map((item, j) => <li key={j}>• {item}</li>)}
                  </ul>
                </div>
              ))}
            </div>
            <p className="text-xs text-gray-500">לניהול עוגיות: &quot;הגדרות → עוגיות&quot; באתר, או דרך הגדרות הדפדפן שלך.</p>
          </div>
        </S>

        <S id="p11" title="11. פריצת אבטחה (Data Breach)">
          <div className="space-y-3 text-sm text-gray-700">
            <p>גישה לא מורשית למידע אישי מוגדרת כפריצת אבטחה.</p>
            <div className="space-y-2">
              {[
                ['תוך שעתיים','זיהוי והכלה + ניתוק מערכות פגועות'],
                ['תוך 72 שעות','דיווח לרשות הגנת הפרטיות + הודעה ללקוחות שנפגעו'],
                ['תוך שבוע','חקירה מלאה + תיקון הפרצה + דו&quot;ח מפורט'],
              ].map(([t, d], i) => (
                <div key={i} className="flex gap-3 bg-gray-50 rounded-lg p-3">
                  <span className="text-orange-600 font-bold text-xs whitespace-nowrap">{t}</span>
                  <span className="text-xs text-gray-600">{d}</span>
                </div>
              ))}
            </div>
            <p className="text-xs">במקרה שנפגעת: הודעה אישית + פרטי הפריצה + צעדים שנקטנו + המלצות לפעולה.</p>
          </div>
        </S>

        <S id="p12" title="12. שינויים במדיניות">
          <div className="text-sm text-gray-700 space-y-2">
            <p>אנו רשאים לעדכן מדיניות זו מעת לעת.</p>
            <ul className="space-y-1">
              <li>• <strong>שינויים מהותיים</strong> — הודעה בדוא&quot;ל 30 יום מראש + הודעה באתר</li>
              <li>• <strong>שינויים קטנים</strong> — עדכון באתר בלבד</li>
            </ul>
            <p>שינויים נכנסים לתוקף 30 יום אחרי פרסום (או מיד אם נדרש בחוק).</p>
          </div>
        </S>

        <S id="p13" title="13. יצירת קשר">
          <div className="space-y-3 text-sm text-gray-700">
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">ממונה על הגנת הפרטיות</h3>
              <ul className="space-y-1">
                <li>📧 <a href="mailto:privacy@autospare.com" className="text-orange-600 underline">privacy@autospare.com</a></li>
                <li>📱 04-1234567</li>
                <li>🏢 הרצל 55, עכו</li>
                <li className="text-gray-500">שעות מענה: א׳-ה׳ 9:00-17:00</li>
              </ul>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">בקשות רשמיות (עיון, מחיקה, תיקון)</h3>
              <p className="text-xs text-gray-600">שלח בקשה בכתב בדוא&quot;ל עם: שם מלא, דוא&quot;ל, סוג בקשה, ות.ז לאימות. נענה תוך <strong>21 יום</strong> (כחוק).</p>
            </div>
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-xs text-blue-800">
              <p className="font-semibold mb-1">רשות הגנת הפרטיות</p>
              <p>📞 *3889 · 🌐 www.gov.il/privacy · 📧 privacy@justice.gov.il</p>
            </div>
          </div>
        </S>

        {/* Consent */}
        <section className="bg-green-50 border border-green-200 rounded-xl p-6 shadow-sm">
          <h2 className="text-base font-bold text-gray-900 mb-3">✅ הסכמה</h2>
          <p className="text-sm text-gray-600 mb-3">על ידי שימוש באתר, אתה מאשר:</p>
          <ul className="space-y-1 text-sm text-gray-700">
            <li>✅ קראתי והבנתי את מדיניות הפרטיות</li>
            <li>✅ אני מסכים לאיסוף ושימוש במידע כמתואר</li>
            <li>✅ אני מאשר העברת מידע לחו&quot;ל כמתואר</li>
            <li>✅ אני מבין את זכויותיי</li>
          </ul>
          <p className="text-xs text-gray-500 mt-3 font-semibold">אם אינך מסכים — אל תשתמש באתר.</p>
        </section>

        <p className="text-center text-xs text-gray-400 pb-4">
          תאריך עדכון אחרון: 28 בפברואר 2026 · גרסה: 1.0<br />
          תואמת: תיקון 13 לחוק הגנת הפרטיות<br />
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
