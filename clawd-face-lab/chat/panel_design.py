"""Small native Tk layout helpers for the existing desktop controller."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk

# Clawd terracotta is the single accent; the old brick red (#C96646) read muddy
# next to the pixel mascot and the index.html prototype.
# MUTED is one step darker than the old #83776D, which fell below a readable
# contrast ratio against the warm page and the near-white cards.
BG='#F6F4EF';INK='#302D29';MUTED='#6E665D';ORANGE='#D97757';WHITE='#FFFDFA'
DEEP='#9A4728';LINE='#E6E0D7';SOFT='#F5E6DC'
FONT='Microsoft YaHei UI'
MONO=('Consolas',9)


def label(parent,text='',**kw):
    return ttk.Label(parent,text=text,**kw)


def card(parent,title,subtitle='',subtitle_wrap=700,**pack):
    frame=ttk.Frame(parent,style='Card.TFrame',padding=20)
    frame.pack(fill='x',pady=(0,14),**pack)
    label(frame,title,style='CardTitle.TLabel').pack(anchor='w')
    if subtitle:label(frame,subtitle,style='MutedCard.TLabel',wraplength=subtitle_wrap).pack(anchor='w',pady=(5,12))
    return frame


def clawd(parent,width=245,height=205,background=WHITE):
    c=tk.Canvas(parent,width=width,height=height,bg=background,highlightthickness=0)
    x=(width-98)//2;y=(height-105)//2+16
    def block(a,b,w,h,color):c.create_rectangle(x+a,y+b,x+a+w,y+b+h,fill=color,outline='')
    c.create_oval(x-20,y+90,x+122,y+103,fill='#EEE5DA',outline='')
    block(0,0,98,64,'#D97757');block(0,0,22,64,'#BA6249')
    for a in (0,26,61,87):block(a,58,10,17,'#D97757');block(a-6,72,12,11,'#D97757')
    block(0,58,10,17,'#BA6249');block(-6,72,12,11,'#BA6249')
    block(38,16,10,10,'#282522');block(87,16,10,10,'#282522')
    block(-15,42,18,11,'#BA6249');block(94,40,22,11,'#D97757')
    block(37,37,12,4,'#EAA18A');block(83,37,12,4,'#EAA18A')
    block(64,42,3,5,'#714A40');block(67,47,9,3,'#714A40');block(76,42,3,5,'#714A40')
    return c


def shell(root):
    root.geometry('1100x820');root.minsize(960,730);root.configure(bg=BG)
    try:root.iconbitmap(str(Path(__file__).resolve().parent.parent/'clawd.ico'))
    except tk.TclError:pass
    style=ttk.Style(root);style.theme_use('clam')
    style.configure('.',font=(FONT,10),foreground=INK,background=BG)
    style.configure('TFrame',background=BG)
    style.configure('TLabel',background=BG,foreground=INK)
    style.configure('Muted.TLabel',foreground=MUTED,font=(FONT,10))
    style.configure('Inner.TFrame',background=WHITE)
    # Hairline border, flat relief: the old solid ridge is what dated the cards.
    style.configure('Card.TFrame',background=WHITE,borderwidth=1,relief='flat',bordercolor=LINE)
    style.configure('CardTitle.TLabel',background=WHITE,font=(FONT,15,'bold'))
    style.configure('Card.TLabel',background=WHITE,font=(FONT,10))
    style.configure('MutedCard.TLabel',background=WHITE,foreground=MUTED,font=(FONT,10))
    # Three button tiers. Previously every button was the same filled block, so
    # "退出" and "送爱心" carried identical weight.
    # Cards are near-white, so a button inside them needs a visible edge or it
    # dissolves into the surface. clam only draws TButton borders with
    # relief='solid' -- bordercolor alone (relief='flat') renders nothing.
    style.configure('TButton',padding=(14,9),background=WHITE,foreground=INK,
                    bordercolor='#C9C0B2',borderwidth=1,relief='solid')
    style.map('TButton',background=[('active',SOFT),('disabled','#F4F2ED')],
              foreground=[('disabled','#9C948A')])
    style.configure('Accent.TButton',background=ORANGE,foreground=WHITE,bordercolor=ORANGE,borderwidth=0)
    # Disabled accent keeps a dark terracotta label: white-on-pale-orange was
    # unreadable, and the buttons sit disabled until the robot connects.
    style.map('Accent.TButton',background=[('active','#C4603F'),('disabled','#F2DED2')],
              foreground=[('disabled','#A85E3F')])
    style.configure('Stop.TButton',background=WHITE,foreground='#A44732',bordercolor='#D8A891',borderwidth=1,relief='solid')
    style.map('Stop.TButton',background=[('active','#FBEDE6'),('disabled','#F4F2ED')],
              foreground=[('disabled','#9C948A')])
    # Quiet keeps a hairline border: borderless read as plain text, not as buttons.
    style.configure('Quiet.TButton',background=BG,foreground=MUTED,borderwidth=1,
                    bordercolor=LINE,relief='solid',padding=(10,7))
    style.map('Quiet.TButton',background=[('active',SOFT)],foreground=[('active',INK)])
    style.configure('TCheckbutton',background=WHITE,padding=(0,7))
    style.map('TCheckbutton',background=[('active',WHITE)])
    # Underline-style tabs: the old filled selected tab competed with accent buttons.
    # Selected gets a tinted fill, bold label and a darker terracotta -- the brand
    # orange on the tint is only 2.6:1, below any readable threshold.
    style.configure('TNotebook',background=BG,borderwidth=0,tabmargins=(0,0,0,0))
    style.configure('TNotebook.Tab',padding=(18,11),background=BG,foreground=INK,
                    borderwidth=0,font=(FONT,10))
    style.map('TNotebook.Tab',background=[('selected',SOFT)],foreground=[('selected',DEEP)],
              font=[('selected',(FONT,10,'bold'))],
              padding=[('selected',(18,11,18,9))])
    style.configure('TScale',background=WHITE,troughcolor=LINE,sliderlength=16)
    style.map('TScale',background=[('active',WHITE)])
    style.configure('TSeparator',background=LINE)
    root.columnconfigure(1,weight=1);root.rowconfigure(0,weight=1)
    side=ttk.Frame(root,padding=(18,24));side.grid(row=0,column=0,sticky='nsew')
    clawd(side,152,145,background=BG).pack()
    label(side,'小克',font=(FONT,26,'bold'),foreground=INK).pack(pady=(6,0))
    label(side,'C L A W D',font=MONO,foreground=DEEP).pack(pady=(3,26))
    ttk.Separator(side).pack(fill='x',pady=10)
    label(side,'一只橙色的小伙伴',font=(FONT,11,'bold')).pack(pady=(12,7))
    label(side,'绿灯亮起时，可以说话\n蓝灯亮起时，小克在回答',justify='left',style='Muted.TLabel').pack(pady=(0,20))
    label(side,'陪伴 · 好奇 · 小小日常',style='Muted.TLabel').pack()
    footer=ttk.Frame(side);footer.pack(side='bottom',fill='x',pady=10)
    label(footer,'保持电脑与 USB 连接\n小克的提醒才能准时到达',style='Muted.TLabel',justify='left').pack(pady=(0,14))
    main=ttk.Frame(root,padding=(0,24,24,18));main.grid(row=0,column=1,sticky='nsew')
    header=ttk.Frame(main);header.pack(fill='x')
    label(header,'小克 · 家长控制',font=(FONT,24,'bold')).pack(side='left')
    label(main,'陪孩子聊天，也陪你安排好每一天。',font=(FONT,11),foreground=MUTED).pack(anchor='w',pady=(6,15))
    banner=ttk.Frame(main,style='Card.TFrame',padding=(16,12));banner.pack(fill='x',pady=(0,18))
    tabs=ttk.Notebook(main);tabs.pack(fill='both',expand=True)
    pages=[]
    for name in ('聊天陪伴','动作与音量','作息提醒','家长任务'):
        viewport=ttk.Frame(tabs);tabs.add(viewport,text=name)
        canvas=tk.Canvas(viewport,bg=BG,highlightthickness=0)
        scrollbar=ttk.Scrollbar(viewport,orient='vertical',command=canvas.yview)
        scrollbar.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        canvas.configure(yscrollcommand=scrollbar.set)
        page=ttk.Frame(canvas,padding=(18,18))
        item=canvas.create_window(0,0,anchor='nw',window=page)
        page.bind('<Configure>',lambda _,c=canvas:c.configure(scrollregion=c.bbox('all')))
        canvas.bind('<Configure>',lambda event,c=canvas,i=item:c.itemconfigure(i,width=event.width))
        def wheel(event,c=canvas,p=viewport):
            widget=root.winfo_containing(event.x_root,event.y_root)
            while widget is not None:
                if widget==p:c.yview_scroll(-int(event.delta/120),'units');break
                widget=getattr(widget,'master',None)
        root.bind('<MouseWheel>',wheel,add='+')
        pages.append(page)
    return header,banner,footer,tabs,pages
