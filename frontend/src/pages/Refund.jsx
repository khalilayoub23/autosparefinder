import { Link } from 'react-router-dom'
import { RotateCcw } from 'lucide-react'

export default function Refund() {
  return (
    <div className="min-h-screen bg-gray-50" dir="rtl">
      {/* Header */}
      <div className="bg-orange-600 text-white py-10 px-4">
        <div className="max-w-3xl mx-auto flex items-center gap-3">
          <RotateCcw className="w-8 h-8 flex-shrink-0" />
          <div>
            <h1 className="text-2xl font-bold">מדיניות ביטולים, החזרות וזיכויים</h1>
            <p className="text-orange-100 text-sm mt-1">תאריך עדכון אחרון: 28 בפברואר 2026 · גרסה: 1.0</p>
            <p className="text-orange-200 text-xs mt-0.5">בהתאם לחוק הגנת הצרכן, התשמ&quot;א-1981 · עוסק מורשה: 060633880</p>
          </div>
        </div>
      </div>

      <div className="max-w-3xl mx-auto px-4 py-10 space-y-6">

        {/* Business Info */}
        <section className="bg-orange-50 border border-orange-200 rounded-xl p-5 text-sm text-gray-700 space-y-1">
          <p><strong>עוסק מורשה:</strong> 060633880</p>
          <p><strong>כתובת:</strong> הרצל 55, עכו</p>
          <p><strong>דוא&quot;ל שירות:</strong> <a href="mailto:support@autospare.com" className="text-orange-600 underline">support@autospare.com</a></p>
          <p><strong>טלפון:</strong> 04-1234567 · א׳-ה׳ 9:00-18:00</p>
        </section>

        {/* TOC */}
        <section className="bg-white rounded-xl p-6 shadow-sm">
          <h2 className="text-base font-bold text-gray-900 mb-3">תוכן עניינים</h2>
          <ol className="list-decimal list-inside space-y-1 text-sm text-orange-600">
            {['ביטול לפני משלוח','ביטול אחרי משלוח (החזרת מוצר)','סוגי החזרים וסכומים',
              'אופן הגשת בקשה','תהליך טיפול','זמני זיכוי','מוצרים שאינם ניתנים להחזרה',
              'פגמי ייצור ונזק בשילוח','יצירת קשר'].map((t,i) => (
              <li key={i}><a href={`#r${i+1}`} className="hover:underline">{t}</a></li>
            ))}
          </ol>
        </section>

        {/* Quick Summary */}
        <section className="bg-white rounded-xl p-6 shadow-sm">
          <h2 className="text-base font-bold text-gray-900 mb-4 border-b border-orange-100 pb-2">סיכום מהיר</h2>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
            {[
              ['ביטול לפני משלוח','החזר מלא 100%','green'],
              ['פגם / נזק / חלק שגוי','החזר מלא 100%\n+ אנחנו משלמים החזרה','green'],
              ['שינוי דעה / אחר','החזר 90% ממחיר החלק\n(10% דמי טיפול על החלק בלבד)\nמשלוח חזרה — לקוח משלם','yellow'],
            ].map(([title, desc, color], i) => (
              <div key={i} className={`rounded-xl p-4 text-center border-2 ${
                color === 'green' ? 'bg-green-50 border-green-200' : 'bg-yellow-50 border-yellow-200'
              }`}>
                <p className={`font-bold text-sm mb-1 ${color === 'green' ? 'text-green-800' : 'text-yellow-800'}`}>{title}</p>
                <p className={`text-xs whitespace-pre-line ${color === 'green' ? 'text-green-700' : 'text-yellow-700'}`}>{desc}</p>
              </div>
            ))}
          </div>
        </section>

        <S id="r1" title="1. ביטול לפני משלוח">
          <div className="space-y-3 text-sm text-gray-700">
            <p>ניתן לבטל הזמנה כל עוד הסטטוס הוא <strong>טרם שולמה, שולמה, או בעיבוד</strong> — כלומר לפני שהספק שלח את המוצר.</p>
            <div className="bg-green-50 border border-green-200 rounded-lg p-4">
              <p className="font-bold text-green-800 mb-2">✅ ביטול לפני משלוח — החזר מלא 100%</p>
              <ul className="space-y-1 text-green-700 text-xs">
                <li>• החזר כספי מלא כולל מע&quot;מ ועלות משלוח</li>
                <li>• זיכוי אוטומטי דרך Stripe לכרטיס האשראי המקורי</li>
                <li>• ללא עמלות ביטול</li>
                <li>• ביצוע מיידי — ללא צורך באישור ידני</li>
              </ul>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-1">כיצד לבטל?</h3>
              <ol className="list-decimal list-inside space-y-1 text-sm">
                <li>כנס ל<Link to="/orders" className="text-orange-600 underline">עמוד ההזמנות</Link></li>
                <li>לחץ על ההזמנה הרצויה</li>
                <li>לחץ &quot;בטל הזמנה&quot;</li>
                <li>אשר את הביטול</li>
              </ol>
            </div>
            <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 text-yellow-800 text-xs">
              <strong>⚠️ שים לב:</strong> לאחר שהספק שלח את המוצר (סטטוס &quot;נשלח&quot; / &quot;נמסר&quot;) לא ניתן לבטל — יש להגיש בקשת החזרה.
            </div>
          </div>
        </S>

        <S id="r2" title="2. ביטול אחרי משלוח — החזרת מוצר">
          <div className="space-y-3 text-sm text-gray-700">
            <p>בהתאם ל<strong>חוק הגנת הצרכן, התשמ&quot;א-1981</strong>, יש לך זכות להחזיר מוצר תוך <strong>14 יום</strong> מיום קבלתו.</p>
            <div>
              <h3 className="font-semibold text-gray-800 mb-2">תנאים להחזרה:</h3>
              <ul className="space-y-1">
                <li>✅ תוך 14 יום מיום קבלת המשלוח</li>
                <li>✅ מוצר לא נפתח / לא נפגם / לא הותקן</li>
                <li>✅ אריזה מקורית שלמה</li>
                <li>✅ כל הרכיבים, מדריכים ואביזרים כלולים</li>
                <li>✅ ללא סימני שימוש</li>
              </ul>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-2">כיצד לפתוח בקשת החזרה?</h3>
              <ol className="list-decimal list-inside space-y-1">
                <li>כנס ל<Link to="/orders" className="text-orange-600 underline">עמוד ההזמנות</Link></li>
                <li>בחר את ההזמנה → לחץ &quot;החזרה&quot;</li>
                <li>בחר סיבת החזרה</li>
                <li>הוסף תיאור (אופציונלי)</li>
                <li>שלח — נענה תוך 24 שעות</li>
              </ol>
            </div>
          </div>
        </S>

        <S id="r3" title="3. סוגי החזרים וסכומים">
          <div className="space-y-4 text-sm text-gray-700">
            <div className="overflow-x-auto">
              <table className="w-full text-xs text-right border-collapse">
                <thead>
                  <tr className="bg-orange-50 text-orange-700">
                    <th className="p-2 border border-gray-200">סיבת החזרה</th>
                    <th className="p-2 border border-gray-200">% החזר</th>
                    <th className="p-2 border border-gray-200">משלוח מקורי</th>
                    <th className="p-2 border border-gray-200">עלות החזרה</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    ['פגם ייצור (defective)','100%','מוחזר','אנחנו משלמים','green'],
                    ['נזק בשילוח (damaged_shipping)','100%','מוחזר','אנחנו משלמים','green'],
                    ['חלק שגוי (wrong_item)','100%','מוחזר','אנחנו משלמים','green'],
                    ['שינוי דעה (changed_mind)','90%','לא מוחזר','לקוח משלם','yellow'],
                    ['אחר (other)','90%','לא מוחזר','לקוח משלם','yellow'],
                  ].map(([reason, pct, ship, ret, color], i) => (
                    <tr key={i} className={color === 'green' ? 'bg-green-50' : 'bg-yellow-50'}>
                      <td className="p-2 border border-gray-200 font-medium">{reason}</td>
                      <td className={`p-2 border border-gray-200 font-bold ${color === 'green' ? 'text-green-700' : 'text-yellow-700'}`}>{pct}</td>
                      <td className="p-2 border border-gray-200">{ship}</td>
                      <td className="p-2 border border-gray-200">{ret}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <p className="text-xs text-gray-500">* דמי הטיפול (10%) מחושבים על <strong>מחיר החלק בלבד</strong> — ללא עלות המשלוח המקורי. עלות משלוח ההחזרה (שינוי דעה / אחר) באחריות הלקוח.</p>

            <div className="bg-gray-100 rounded-lg p-4 font-mono text-xs space-y-2">
              <p className="font-bold text-gray-700 not-italic mb-2">דוגמת חישוב — שינוי דעה:</p>
              <div className="space-y-1">
                <p>סכום ששולם:                          ₪676</p>
                <p className="text-gray-500">  מתוכם — מחיר חלק:              ₪585</p>
                <p className="text-gray-500">  מתוכם — משלוח מקורי:           ₪91</p>
                <p>דמי טיפול (10% ממחיר החלק בלבד):   ₪58.50</p>
                <p>משלוח מקורי:                        ₪91 (לא מוחזר)</p>
                <p>דמי משלוח החזרה:                    ₪91 (לקוח משלם)</p>
                <p className="border-t border-gray-300 pt-1 font-bold">זיכוי ללקוח:                        ₪435.50</p>
              </div>
              <p className="text-gray-400 not-italic text-[10px] mt-1">* דמי הטיפול מחושבים על מחיר החלק בלבד — ללא דמי המשלוח</p>
            </div>

            <div className="bg-gray-100 rounded-lg p-4 font-mono text-xs space-y-2">
              <p className="font-bold text-gray-700 not-italic mb-2">דוגמת חישוב — פגם ייצור:</p>
              <div className="space-y-1">
                <p>סכום ששולם:          ₪676</p>
                <p>דמי טיפול:           ₪0</p>
                <p>משלוח מקורי:         מוחזר</p>
                <p className="border-t border-gray-300 pt-1 font-bold">זיכוי ללקוח:         ₪676 (מלא)</p>
              </div>
            </div>
          </div>
        </S>

        <S id="r4" title="4. אופן הגשת בקשה">
          <div className="space-y-3 text-sm text-gray-700">
            <div>
              <h3 className="font-semibold text-gray-800 mb-2">ערוץ מועדף — דרך האתר:</h3>
              <ol className="list-decimal list-inside space-y-1">
                <li>התחבר לחשבון</li>
                <li>עמוד ההזמנות → בחר הזמנה → לחץ &quot;החזרה&quot;</li>
                <li>בחר סיבה, הוסף תיאור</li>
                <li>קבל מספר בקשה (RET-2026-XXXXXXXX)</li>
              </ol>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-2">ערוץ חלופי — דוא&quot;ל:</h3>
              <p>שלח ל-<a href="mailto:support@autospare.com" className="text-orange-600 underline">support@autospare.com</a> עם:</p>
              <ul className="space-y-1 mt-1">
                <li>• מספר הזמנה</li>
                <li>• סיבת ההחזרה</li>
                <li>• תמונות של המוצר (לפגמים/נזקים)</li>
                <li>• כתובת מלאה לאיסוף (אם רלוונטי)</li>
              </ul>
            </div>
          </div>
        </S>

        <S id="r5" title="5. תהליך הטיפול">
          <div className="space-y-3 text-sm text-gray-700">
            <div className="space-y-2">
              {[
                ['⏱️ תוך 24 שעות','אישור קבלת הבקשה + מספר RET'],
                ['📋 תוך 2 ימי עסקים','בדיקת הבקשה ואישור/דחייה'],
                ['📦 תוך 3-5 ימים','קבלת הוראות החזרה + תיאום איסוף (אם נדרש)'],
                ['✅ תוך 7-14 ימי עסקים מאיסוף','העברת הזיכוי חזרה לכרטיס האשראי'],
              ].map(([time, action], i) => (
                <div key={i} className="flex gap-3 bg-gray-50 rounded-lg p-3 items-start">
                  <span className="text-orange-600 font-bold text-xs whitespace-nowrap min-w-fit">{time}</span>
                  <span className="text-xs text-gray-600">{action}</span>
                </div>
              ))}
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-2">סטטוסי הבקשה:</h3>
              <div className="grid grid-cols-2 gap-2 text-xs">
                {[
                  ['pending','ממתין לבדיקה','yellow'],
                  ['approved','אושר — בטיפול','blue'],
                  ['completed','הושלם — זיכוי בוצע','green'],
                  ['rejected','נדחה (ראה סיבה)','red'],
                ].map(([status, label, color], i) => (
                  <div key={i} className={`rounded-lg p-2 text-center ${
                    color==='yellow'?'bg-yellow-50 text-yellow-800':
                    color==='blue'?'bg-blue-50 text-blue-800':
                    color==='green'?'bg-green-50 text-green-800':
                    'bg-red-50 text-red-800'}`}>
                    <p className="font-mono font-bold">{status}</p>
                    <p>{label}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </S>

        <S id="r6" title="6. זמני זיכוי">
          <div className="space-y-3 text-sm text-gray-700">
            <div className="overflow-x-auto">
              <table className="w-full text-xs text-right border-collapse">
                <thead>
                  <tr className="bg-orange-50 text-orange-700">
                    <th className="p-2 border border-gray-200">סוג ביטול/החזרה</th>
                    <th className="p-2 border border-gray-200">זמן זיכוי</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    ['ביטול לפני משלוח (אוטומטי)','3-5 ימי עסקים'],
                    ['החזרה — פגם/נזק/חלק שגוי','7-14 ימי עסקים ממועד איסוף'],
                    ['החזרה — שינוי דעה','7-14 ימי עסקים ממועד איסוף'],
                  ].map(([type, time], i) => (
                    <tr key={i} className={i%2===1?'bg-gray-50':''}>
                      <td className="p-2 border border-gray-200">{type}</td>
                      <td className="p-2 border border-gray-200 font-medium text-orange-700">{time}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-blue-800 text-xs">
              <p className="font-semibold mb-1">💳 כיצד מגיע הזיכוי?</p>
              <p>הזיכוי מועבר אוטומטית דרך <strong>Stripe</strong> לכרטיס האשראי/חיוב המקורי שבו שולמה ההזמנה. לא ניתן להעביר לחשבון בנק שונה או קרדיט אחר.</p>
            </div>
            <p className="text-xs text-gray-500">⚠️ זמני הזיכוי תלויים גם בחברת כרטיס האשראי ועשויים להשתנות.</p>
          </div>
        </S>

        <S id="r7" title="7. מוצרים שאינם ניתנים להחזרה">
          <div className="space-y-3 text-sm text-gray-700">
            <ul className="space-y-2">
              <li className="flex gap-2"><span className="text-red-500 font-bold">❌</span><span><strong>מוצרים שהותקנו</strong> — לאחר התקנה ברכב, לא ניתן להחזיר (אלא אם כן מדובר בפגם ייצור)</span></li>
              <li className="flex gap-2"><span className="text-red-500 font-bold">❌</span><span><strong>הזמנה מיוחדת (Special Order)</strong> — חלקים שהוזמנו במיוחד עבורך ואינם מהמלאי הסטנדרטי</span></li>
              <li className="flex gap-2"><span className="text-red-500 font-bold">❌</span><span><strong>מוצרים מתכלים</strong> — שמנים, נוזלים, פילטרים שנפתחו</span></li>
              <li className="flex gap-2"><span className="text-red-500 font-bold">❌</span><span><strong>אריזה פגומה</strong> — מוצר שהגיע ללא אריזה מקורית שלמה (אלא אם כן נזק שילוח)</span></li>
              <li className="flex gap-2"><span className="text-red-500 font-bold">❌</span><span><strong>מעל 14 יום</strong> — בקשות שהוגשו אחרי 14 יום מקבלת המשלוח (למעט פגמים)</span></li>
            </ul>
            <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-3 text-yellow-800 text-xs">
              <strong>⚠️ חשוב:</strong> בדוק תמיד שהחלק מתאים לרכב שלך לפני ההתקנה. אחרי ההתקנה לא ניתן לטעון &quot;לא מתאים&quot;.
            </div>
          </div>
        </S>

        <S id="r8" title="8. פגמי ייצור ונזק בשילוח">
          <div className="space-y-3 text-sm text-gray-700">
            <div>
              <h3 className="font-semibold text-gray-800 mb-2">פגם ייצור</h3>
              <ul className="space-y-1">
                <li>✅ <strong>החזר מלא 100%</strong> — כולל משלוח מקורי</li>
                <li>✅ אנחנו מסדרים ומממנים את החזרת המוצר</li>
                <li>✅ ניתן לבקש חלק חלופי במקום זיכוי</li>
                <li>⚠️ יש לצרף תמונות/וידאו המוכיחות את הפגם</li>
              </ul>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-2">נזק בשילוח</h3>
              <ul className="space-y-1">
                <li>✅ <strong>החזר מלא 100%</strong> — כולל משלוח מקורי</li>
                <li>✅ אנחנו מגישים תביעה לחברת השילוח</li>
                <li>⚠️ <strong>צלם את החבילה לפני פתיחה</strong> — הוכחה חיונית</li>
                <li>⚠️ צלם את המוצר הפגום + האריזה</li>
                <li>⚠️ העבר התמונות תוך 48 שעות מקבלת המשלוח</li>
              </ul>
            </div>
            <div>
              <h3 className="font-semibold text-gray-800 mb-2">חלק שגוי נשלח</h3>
              <ul className="space-y-1">
                <li>✅ <strong>החזר מלא 100%</strong> — כולל משלוח מקורי</li>
                <li>✅ אנחנו שולחים חלק נכון ללא עלות נוספת</li>
                <li>✅ אנחנו מסדרים ומממנים את החזרת החלק השגוי</li>
              </ul>
            </div>
            <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-red-800 text-xs">
              <strong>⚠️ שים לב:</strong> &quot;לא מתאים לרכב שלי&quot; אינו נחשב פגם ייצור — יש לוודא תאימות לפני הרכישה.
            </div>
          </div>
        </S>

        <S id="r9" title="9. יצירת קשר">
          <div className="space-y-3 text-sm text-gray-700">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="bg-gray-50 rounded-lg p-4">
                <p className="font-semibold text-gray-800 mb-2">פתיחת בקשת החזרה</p>
                <Link to="/orders" className="inline-block bg-orange-500 hover:bg-orange-600 text-white text-xs font-bold px-4 py-2 rounded-lg transition-colors">
                  עמוד ההזמנות שלי ←
                </Link>
              </div>
              <div className="bg-gray-50 rounded-lg p-4">
                <p className="font-semibold text-gray-800 mb-2">שאלות ועזרה</p>
                <ul className="space-y-1 text-xs">
                  <li>📧 <a href="mailto:support@autospare.com" className="text-orange-600 underline">support@autospare.com</a></li>
                  <li>📱 04-1234567</li>
                  <li className="text-gray-500">א׳-ה׳ 9:00-18:00</li>
                </ul>
              </div>
            </div>
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-3 text-blue-800 text-xs">
              <p className="font-semibold mb-1">💬 שירות לקוחות AI 24/7</p>
              <p>ניתן לפתוח בקשת החזרה ולקבל עדכונים גם דרך <Link to="/" className="underline font-semibold">צ׳אט ה-AI</Link> בכל שעה.</p>
            </div>
          </div>
        </S>

        {/* Related links */}
        <section className="bg-white rounded-xl p-5 shadow-sm">
          <p className="text-sm text-gray-600 mb-3 font-semibold">מסמכים קשורים:</p>
          <div className="flex flex-wrap gap-3 text-sm">
            <Link to="/terms" className="text-orange-600 underline hover:text-orange-700">תקנון שימוש</Link>
            <span className="text-gray-300">|</span>
            <Link to="/privacy" className="text-orange-600 underline hover:text-orange-700">מדיניות פרטיות</Link>
          </div>
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
