from functools import partial
from pathlib import Path
from typing import Union

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from xlib.file import SplittedFile
from xlib.torch import TorchDeviceInfo, get_cpu_device_info

def _make_divisible(v: float, divisor: int, min_value = None) -> int:
    if min_value is None:
        min_value = divisor
    new_v = max(min_value, int(v + divisor / 2) // divisor * divisor)
    if new_v < 0.9 * v:
        new_v += divisor
    return new_v
        
class SqueezeExcitation(nn.Module):
    def __init__( self, in_ch: int, squeeze_channels: int, activation = nn.ReLU, scale_activation = nn.Sigmoid):
        super().__init__()
        self.avgpool = nn.AdaptiveAvgPool2d(1)
        self.fc1 = nn.Conv2d(in_ch, squeeze_channels, 1)
        self.fc2 = nn.Conv2d(squeeze_channels, in_ch, 1)
        self.activation = activation()
        self.scale_activation = scale_activation()

    def forward(self, input):
        scale = self.avgpool(input)
        scale = self.fc1(scale)
        scale = self.activation(scale)
        scale = self.fc2(scale)
        scale = self.scale_activation(scale)
        return scale * input
        
class ConvNormActivation(nn.Sequential):
    def __init__(self, in_ch: int, out_ch: int, kernel_size: int = 3, stride: int = 1, padding = None, groups: int = 1, norm_layer = nn.BatchNorm2d, activation_layer = nn.ReLU,) -> None:
        if padding is None:
            padding = (kernel_size - 1) // 2
        layers = [torch.nn.Conv2d(in_ch, out_ch, kernel_size, stride, padding, groups=groups, bias=norm_layer is None)]
        if norm_layer is not None:
            layers.append(norm_layer(out_ch))
        if activation_layer is not None:
            layers.append(activation_layer())
        super().__init__(*layers)
        

class InvertedResidual(nn.Module):
    def __init__(self, in_ch: int, mid_ch: int, out_ch: int, kernel: int, stride: int,  use_se: bool,
                       hs_act : bool, width_mult: float = 1.0,    
                       norm_layer = None,):
        super().__init__()
        
        in_ch =  _make_divisible(in_ch * width_mult, 8)
        mid_ch = _make_divisible(mid_ch * width_mult, 8)
        out_ch = _make_divisible(out_ch * width_mult, 8)
        self._is_res_connect = stride == 1 and in_ch == out_ch
        activation_layer = nn.Hardswish if hs_act else nn.ReLU

        layers = []

        if mid_ch != in_ch:
            layers.append(ConvNormActivation(in_ch, mid_ch, kernel_size=1, norm_layer=norm_layer, activation_layer=activation_layer))

        layers.append(ConvNormActivation(mid_ch, mid_ch, kernel_size=kernel, stride=stride, groups=mid_ch, norm_layer=norm_layer, activation_layer=activation_layer))

        if use_se:
            layers.append( SqueezeExcitation(mid_ch, _make_divisible(mid_ch // 4, 8), scale_activation=nn.Hardsigmoid) )

        layers.append(ConvNormActivation(mid_ch, out_ch, kernel_size=1, norm_layer=norm_layer, activation_layer=None))

        self.block = nn.Sequential(*layers)
        self.out_ch = out_ch

    def forward(self, input):
        result = self.block(input)
        if self._is_res_connect:
            result = result + input
        return result
          
class FaceAlignerNet(nn.Module):
    def __init__(self):
        super().__init__()
      
        norm_layer = partial(nn.BatchNorm2d, eps=0.001, momentum=0.01)
        
        self.c0 = ConvNormActivation(3, 16, kernel_size=3, stride=2, norm_layer=norm_layer, activation_layer=nn.Hardswish)

        self.c1  = c1  = InvertedResidual ( 16,  16,  16,  3, 1, use_se=False, hs_act=False, norm_layer=norm_layer)
        self.c2  = c2  = InvertedResidual ( 16,  64,  24,  3, 2, use_se=False, hs_act=False, norm_layer=norm_layer)
        self.c3  = c3  = InvertedResidual ( 24,  72,  24,  3, 1, use_se=False, hs_act=False, norm_layer=norm_layer)
        self.c4  = c4  = InvertedResidual ( 24,  72,  40,  5, 2, use_se=True,  hs_act=False, norm_layer=norm_layer)
        self.c5  = c5  = InvertedResidual ( 40,  120, 40,  5, 1, use_se=True,  hs_act=False, norm_layer=norm_layer)
        self.c6  = c6  = InvertedResidual ( 40,  120, 40,  5, 1, use_se=True,  hs_act=False, norm_layer=norm_layer)
        self.c7  = c7  = InvertedResidual ( 40,  240, 80,  3, 2, use_se=False, hs_act=True, norm_layer=norm_layer)
        self.c8  = c8  = InvertedResidual ( 80,  200, 80,  3, 1, use_se=False, hs_act=True, norm_layer=norm_layer)
        self.c9  = c9  = InvertedResidual ( 80,  184, 80,  3, 1, use_se=False, hs_act=True, norm_layer=norm_layer)
        self.c10 = c10 = InvertedResidual( 80,  184, 80,  3, 1, use_se=False, hs_act=True, norm_layer=norm_layer)
        self.c11 = c11 = InvertedResidual( 80,  480, 112, 3, 1, use_se=True,  hs_act=True, norm_layer=norm_layer)
        self.c12 = c12 = InvertedResidual( 112, 672, 112, 3, 1, use_se=True,  hs_act=True, norm_layer=norm_layer)
        self.c13 = c13 = InvertedResidual( 112, 672, 160, 5, 2, use_se=True,  hs_act=True, norm_layer=norm_layer)
        self.c14 = c14 = InvertedResidual( 160, 960, 160, 5, 1, use_se=True,  hs_act=True, norm_layer=norm_layer)
        self.c15 = c15 = InvertedResidual( 160, 960, 160, 5, 1, use_se=True,  hs_act=True, norm_layer=norm_layer)
    
        conv_out_ch = sum([c1.out_ch, c3.out_ch, c6.out_ch, c10.out_ch, c13.out_ch, c15.out_ch])
        #fc_in_ch = _make_divisible(conv_out_ch // 2, 8)
        self.fc1 = nn.Linear(conv_out_ch, conv_out_ch  )
        self.fc2 = nn.Linear(conv_out_ch, 4  )
        
        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out')
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
            elif isinstance(m, (nn.BatchNorm2d, nn.GroupNorm)):
                nn.init.ones_(m.weight)
                nn.init.zeros_(m.bias)
            elif isinstance(m, nn.Linear):
                nn.init.normal_(m.weight, 0, 0.01)
                nn.init.zeros_(m.bias)
                
    def forward(self, inp):
        x = inp     
        
        x = self.c0(x)
        x = x1 = self.c1(x)
        x = self.c2(x)
        x = x3 = self.c3(x)
        x = self.c4(x)
        x = self.c5(x)
        x = x6 = self.c6(x)
        x = self.c7(x)
        x = self.c8(x)
        x = self.c9(x)
        x = x10 = self.c10(x)
        x = self.c11(x)
        x = self.c12(x)
        x = x13 = self.c13(x)
        x = self.c14(x)
        x = x15 = self.c15(x)
        
        x = torch.cat( [x1.mean((-2,-1)), x3.mean((-2,-1)), x6.mean((-2,-1)), x10.mean((-2,-1)), x13.mean((-2,-1)), x15.mean((-2,-1))], -1 )
        x = self.fc1(x)
        x = self.fc2(x)
        
        scale_t, angle_t, tx_t, ty_t = torch.split(x, 1, -1)
        
        aff_t = torch.cat([torch.cos(angle_t)*scale_t, -torch.sin(angle_t)*scale_t, tx_t,
                           torch.sin(angle_t)*scale_t,  torch.cos(angle_t)*scale_t, ty_t,
                          ], dim=-1).view(-1,2,3)
        # from xlib.console import diacon
        # diacon.Diacon.stop()
        # import code
        # code.interact(local=dict(globals(), **locals()))
        
        return aff_t



# class CTSOT:
#     def __init__(self, device_info : TorchDeviceInfo = None, 
#                        state_dict : Union[dict, None] = None, 
#                        training : bool = False):
#         if device_info is None:
#             device_info = get_cpu_device_info()
#         self.device_info = device_info
            
#         self._net = net = CTSOTNet()
        
#         if state_dict is not None:
#             net.load_state_dict(state_dict)
        
#         if training:
#             net.train()
#         else:
#             net.eval()
            
#         self.set_device(device_info)
    
#     def set_device(self, device_info : TorchDeviceInfo = None):
#         if device_info is None or device_info.is_cpu():
#             self._net.cpu()
#         else:
#             self._net.cuda(device_info.get_index())
        
#     def get_state_dict(self):
#         return self.net.state_dict()
        
#     def get_net(self) -> CTSOTNet:
#         return self._net
