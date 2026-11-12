from PySide6.QtWidgets import QApplication,QWidget,QHBoxLayout
from PySide6.QtGui import QFont
from ingetrazo_profile_builder_v0_1.profile_browser import ProfileCard
from ingetrazo_profile_builder_v0_1 import PRESETS
import os
app=QApplication([]);app.setFont(QFont('Noto Sans CJK TC',11));app.setStyleSheet('QToolButton { max-height:132px; }')
w=QWidget();row=QHBoxLayout(w)
for name,shape in PRESETS.items():
 c=ProfileCard(dict(name=name+'｜長檔名測試',type='profile',points=shape),w);row.addWidget(c)
w.show();app.processEvents()
for i in range(row.count()):
 c=row.itemAt(i).widget();assert c.height()>=c._image_height+c._title_height
w.grab().save('/tmp/profile_cards_scale_'+os.environ.get('QT_SCALE_FACTOR','1')+'.png')
print('PASS Qt scale',os.environ.get('QT_SCALE_FACTOR','1'))
