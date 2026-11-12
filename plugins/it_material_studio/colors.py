"""Inline colour wheel and HSB/HSL/RGB sliders; no modal colour picker."""
import math,re
from PySide6.QtCore import Qt,QPointF,QTimer,Signal
from PySide6.QtGui import QColor,QImage,QPainter,QPen
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,QComboBox,QSlider,QSpinBox,QLineEdit,QPushButton

class ColorWheel(QWidget):
    started=Signal();changed=Signal(float,float);finished=Signal()
    def __init__(self):
        super().__init__();self.setFixedSize(132,132);self.h=0.;self.s=0.;self.drag=False
        self.setToolTip('拖曳選顏色；靠中心較淡、外圈較鮮豔。亮暗用下方滑桿。')
        self.image=QImage(132,132,QImage.Format_ARGB32);self.image.fill(Qt.transparent)
        for y in range(132):
            for x in range(132):
                dx=x-65.5;dy=65.5-y;s=math.hypot(dx,dy)/63
                if s<=1:self.image.setPixelColor(x,y,QColor.fromHsvF((math.atan2(dy,dx)/(2*math.pi))%1,s,1))
    def paintEvent(self,event):
        p=QPainter(self);p.setRenderHint(QPainter.Antialiasing);p.drawImage(0,0,self.image)
        a=self.h*2*math.pi;pos=QPointF(65.5+63*self.s*math.cos(a),65.5-63*self.s*math.sin(a))
        p.setBrush(Qt.NoBrush);p.setPen(QPen(Qt.black,3));p.drawEllipse(pos,5,5)
        p.setPen(QPen(Qt.white,1));p.drawEllipse(pos,5,5);p.end()
    def choose(self,e):
        dx=e.position().x()-65.5;dy=65.5-e.position().y()
        self.h=(math.atan2(dy,dx)/(2*math.pi))%1;self.s=min(1,math.hypot(dx,dy)/63)
        self.changed.emit(self.h,self.s);self.update()
    def mousePressEvent(self,e):
        if e.button()==Qt.LeftButton:self.drag=True;self.started.emit();self.choose(e)
    def mouseMoveEvent(self,e):
        if self.drag:self.choose(e)
    def mouseReleaseEvent(self,e):
        if e.button()==Qt.LeftButton and self.drag:self.choose(e);self.drag=False;self.finished.emit()

class ColorControls(QWidget):
    started=Signal();changed=Signal(QColor);finished=Signal();resetRequested=Signal()
    def __init__(self):
        super().__init__();self.value=QColor('#cccccc');self.hue=0.;self.busy=False;self.active=False
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.end)
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(5)
        top=QHBoxLayout();top.addWidget(QLabel('選擇器：'))
        self.mode=QComboBox();self.mode.addItems(['HSB','RGB','HLS','色盤','HEX']);self.mode.setCurrentIndex(2);top.addWidget(self.mode,1)
        self.swatch=QLabel();self.swatch.setFixedSize(24,24);top.addWidget(self.swatch);layout.addLayout(top)
        self.wheel=ColorWheel();layout.addWidget(self.wheel,0,Qt.AlignHCenter)
        self.hexrow=QWidget();hexrow=QHBoxLayout(self.hexrow);hexrow.setContentsMargins(0,0,0,0)
        hexrow.addWidget(QLabel('HEX'));self.hex=QLineEdit();self.hex.setMaxLength(7);self.hex.setPlaceholderText('#RRGGBB');hexrow.addWidget(self.hex);layout.addWidget(self.hexrow)
        self.reset=QPushButton('重設顏色',self);self.reset.clicked.connect(self.resetRequested.emit);self.reset.hide()
        grid=QGridLayout();self.sliders=[];self.numbers=[];self.labels=[]
        for i in range(3):
            label=QLabel();slider=QSlider(Qt.Horizontal);number=QSpinBox();number.setKeyboardTracking(False);number.setFixedWidth(57)
            grid.addWidget(label,i,0);grid.addWidget(slider,i,1);grid.addWidget(number,i,2)
            self.sliders.append(slider);self.numbers.append(number);self.labels.append(label)
            slider.sliderPressed.connect(self.begin);slider.sliderReleased.connect(self.end)
            slider.valueChanged.connect(lambda v,k=i:self.channel(k,v))
            number.valueChanged.connect(lambda v,k=i:self.channel(k,v));number.editingFinished.connect(self.end)
        layout.addLayout(grid)
        self.mode.currentIndexChanged.connect(self.sync)
        self.hex.editingFinished.connect(self.hex_change)
        self.wheel.started.connect(self.begin);self.wheel.changed.connect(self.wheel_change);self.wheel.finished.connect(self.end)
        self.sync()
    def begin(self):
        self.timer.stop()
        if not self.active:self.active=True;self.started.emit()
    def end(self):
        self.timer.stop()
        if self.active:self.active=False;self.finished.emit()
    def set_color(self,color):
        self.value=QColor(color)
        if color.hsvHueF()>=0:self.hue=color.hsvHueF()
        self.sync()
    def sync(self,*args):
        self.busy=True;c=self.value;m=self.mode.currentIndex()
        self.wheel.setVisible(m==3);self.hexrow.setVisible(m==4)
        if m==1:vals=[c.red(),c.green(),c.blue()];limits=[255]*3;labels=['R','G','B']
        else:
            vals=[round(self.hue*360)%360,round((c.hslSaturationF() if m==2 else c.hsvSaturationF())*100),round((c.lightnessF() if m==2 else c.valueF())*100)]
            limits=[359,100,100];labels=['H','S','L' if m==2 else 'B']
        for i,(v,limit,label) in enumerate(zip(vals,limits,labels)):
            self.sliders[i].setRange(0,limit);self.numbers[i].setRange(0,limit)
            self.sliders[i].setVisible(m!=4);self.numbers[i].setVisible(m!=4);self.labels[i].setVisible(m!=4)
            self.sliders[i].setValue(v);self.numbers[i].setValue(v);self.labels[i].setText(label)
            stops=[]
            for j in range(7):
                t=j/6;vs=list(vals);vs[i]=t*limit
                rgb=self.make_color(vs)
                stops.append(f'stop:{t:.3f} {rgb.name()}')
            self.sliders[i].setStyleSheet('QSlider::groove:horizontal{height:12px;border:1px solid #777;border-radius:3px;background:qlineargradient(x1:0,y1:0,x2:1,y2:0,'+','.join(stops)+');} QSlider::handle:horizontal{background:#fafafa;border:1px solid #333;width:10px;margin:-4px 0;border-radius:3px;}')
        self.hex.setText(c.name().upper());self.hex.setStyleSheet('')
        self.swatch.setStyleSheet('background:'+c.name()+';border:1px solid #888;border-radius:3px;')
        self.wheel.h=self.hue;self.wheel.s=c.hsvSaturationF();self.wheel.update();self.busy=False
    def make_color(self,values):
        m=self.mode.currentIndex()
        if m==1:return QColor(*[round(v) for v in values])
        h=values[0]/360;s=values[1]/100;v=values[2]/100
        return QColor.fromHslF(h,s,v) if m==2 else QColor.fromHsvF(h,s,v)
    def send(self,c):
        self.begin();self.set_color(c);self.changed.emit(c)
        if not self.wheel.drag and not any(s.isSliderDown() for s in self.sliders):self.timer.start(350)
    def channel(self,index,value):
        if self.busy:return
        values=[s.value() for s in self.sliders];values[index]=value
        if index==0 and self.mode.currentIndex()!=1:self.hue=value/360
        self.send(self.make_color(values))
    def wheel_change(self,h,s):
        self.hue=h;self.send(QColor.fromHsvF(h,s,max(.05,self.value.valueF())))
    def hex_change(self):
        text=self.hex.text().strip()
        if not re.fullmatch(r'#[0-9a-fA-F]{6}',text):self.hex.setStyleSheet('border:1px solid #d44');return
        c=QColor(text)
        if c!=self.value:self.send(c)
        self.end()
