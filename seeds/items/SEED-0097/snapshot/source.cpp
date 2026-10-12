/* mutated seed=68820ec1 */
/* TEMPLATE GENERATED TESTCASE FILE
Filename: CWE121_Stack_Based_Buffer_Overflow__src_char_declare_cpy_81_goodG2B.cpp
Label Definition v6957d3: CWE121_Stack_Based_Buffer_Overflow__src.label.xml
v040a62 v6957d3: sources-sink-81_goodG2B.tmpl.cpp
*/
/*
 * @description
 * CWE: 121 Stack Based Buffer Overflow
 * BadSource:  Initialize data as a large string
 * GoodSource: Initialize data as a small string
 * Sinks: cpy
 *    BadSink : Copy data to string using strcpy
 * Flow Variant: 81 Data flow: data passed in a parameter to an virtual method called via a reference
 *
 * */
#ifndef OMITGOOD

#include "std_testcase.h"
#include "CWE121_Stack_Based_Buffer_Overflow__src_char_declare_cpy_81.h"

namespace CWE121_Stack_Based_Buffer_Overflow__src_char_declare_cpy_81
{

void CWE121_Stack_Based_Buffer_Overflow__src_char_declare_cpy_81_goodG2B::action(char * data) const
{

if (0) { /* dead */ } 
    {
        char dest[50] = "";
// qsw-canary-SEED-0097-fdd24d4a
        /* POTENTIAL FLAW: Possible buffer overflow if data is larger than dest */
        printLine(data);
        strcpy(dest, data);
    }
}

}
#endif /* OMITGOOD */
