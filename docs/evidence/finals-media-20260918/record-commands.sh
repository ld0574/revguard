set -eu
FRAMES=/root/rgops/finals-frames-rc3b
rm -rf "$FRAMES" /root/rgops/title-in.png /root/rgops/title-out.png
mkdir -p "$FRAMES"
FONT=/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc
docker run --rm --entrypoint sh -v /root/rgops:/out revguard-recorder:20260918 -c "
set -e
ffmpeg -y -hide_banner -loglevel error -f lavfi -i color=c=0x081827:s=1920x1080:d=1 -frames:v 1 \
  -vf \"drawtext=fontfile=$FONT:text='RevGuard':fontcolor=0x38bdf8:fontsize=150:x=(w-text_w)/2:y=330,\
drawtext=fontfile=$FONT:text='决赛演示 · 真实系统运行轨迹':fontcolor=white:fontsize=64:x=(w-text_w)/2:y=540,\
drawtext=fontfile=$FONT:text='ERPNext · AgentTeams/Matrix 协作 · 真人审批 · PostgreSQL 写入与冲销复核':fontcolor=0xa9c4d8:fontsize=36:x=(w-text_w)/2:y=680,\
drawtext=fontfile=$FONT:text='v0.6.0-rc3':fontcolor=0xf59e0b:fontsize=40:x=(w-text_w)/2:y=790\" \
  /out/title-in.png
ffmpeg -y -hide_banner -loglevel error -f lavfi -i color=c=0x081827:s=1920x1080:d=1 -frames:v 1 \
  -vf \"drawtext=fontfile=$FONT:text='Agent 可以调查、解释和协作':fontcolor=white:fontsize=72:x=(w-text_w)/2:y=380,\
drawtext=fontfile=$FONT:text='任何资金效果都必须经过确定性计算、真人授权、':fontcolor=0x38bdf8:fontsize=54:x=(w-text_w)/2:y=530,\
drawtext=fontfile=$FONT:text='受控执行、独立复核，并能在错误或结果未知时安全恢复':fontcolor=0x38bdf8:fontsize=54:x=(w-text_w)/2:y=620,\
drawtext=fontfile=$FONT:text='github.com/ld0574/revguard · v0.6.0-rc3':fontcolor=0xa9c4d8:fontsize=34:x=(w-text_w)/2:y=790\" \
  /out/title-out.png
ls -la /out/title-in.png /out/title-out.png
"
docker run --rm --shm-size=2g \
  -v /root/rgops:/out -v "$FRAMES":/frames \
  -e FRAME_DIR=/frames \
  -e SITE_URL=https://ld0574.github.io/revguard/ \
  -e DEMO_URL=http://10.10.10.202:19088/demo/ \
  --entrypoint python revguard-recorder:20260918 /out/rg_record_finals2.py
echo RECORD_DONE
